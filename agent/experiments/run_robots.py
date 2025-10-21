import atexit
import asyncio
import json
import logging
import signal
import threading
import time
from queue import Queue, Empty
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import numpy as np

import tyro
import zmq.error
from omegaconf import OmegaConf

from gello.utils.launch_utils import instantiate_from_dict

logger = logging.getLogger(__name__)

# Embedded Flask API
from flask import Flask, jsonify, request
from flask_cors import CORS
from aiortc import RTCPeerConnection, RTCSessionDescription

# Global variable to store agent instance
agent = None
launched = False
curr_tracking_robot_webrtc = None
# Full endpoint to send joint states to (constructed from streamAddress + "/intervene")
webrtc_intervene_endpoint = None
currently_intervening = False

# WebRTC state (client-side)
webrtc_pc = None
webrtc_dc = None
webrtc_connected = False
webrtc_loop = None
webrtc_loop_thread: threading.Thread | None = None


def _webrtc_send_json(payload: dict) -> None:
    """Thread-safe schedule of DataChannel send on the WebRTC asyncio loop."""
    global webrtc_dc, webrtc_loop
    if webrtc_loop is None or webrtc_dc is None:
        return
    message = json.dumps(payload)
    def _do_send() -> None:
        try:
            if getattr(webrtc_dc, "readyState", "") == "open":
                webrtc_dc.send(message)
        except Exception:
            logger.debug("[webrtc] send failed", exc_info=True)
    try:
        webrtc_loop.call_soon_threadsafe(_do_send)
    except Exception:
        logger.debug("[webrtc] failed to schedule send", exc_info=True)

# Thread-based intervention system
intervention_thread: threading.Thread | None = None
intervention_stop_event = threading.Event()
intervention_tasks: "Queue[tuple]" = Queue()
intervention_lock = threading.Lock()

# Global variables for cleanup
active_threads = []
active_servers = []
cleanup_in_progress = False


def cleanup():
    """Clean up resources before exit."""
    global cleanup_in_progress
    if cleanup_in_progress:
        logger.debug("cleanup() already in progress; returning")
        return
    cleanup_in_progress = True

    # agent.agent_left.close()
    print("closing agent_right")
    agent.agent_right.close()

    logger.info("Cleaning up resources...")
    for server in active_servers:
        try:
            if hasattr(server, "close"):
                logger.debug("Closing server %s", server)
                server.close()
        except Exception as e:
            logger.exception("Error closing server: %s", e)

    for thread in active_threads:
        if thread.is_alive():
            logger.debug("Joining active thread %s", thread.name)
            thread.join(timeout=2)

    logger.info("Cleanup completed.")


if launched:
    logger.info("launch_robots() called but robots already launched; skipping")

# Register cleanup handlers
# If terminated without cleanup, can leave ZMQ sockets bound causing "address in use" errors or resource leaks
def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.warning("Received signal %s; performing cleanup and exiting", signum)
    cleanup()
    import os

    os._exit(0)

atexit.register(cleanup)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

left_config_path = "/home/p/Desktop/intervention/agent/configs/yam_active.yaml"
right_config_path = "/home/p/Desktop/intervention/agent/configs/yam_active_l.yaml"
logger.info("Launching robots with configs: left=%s right=%s", left_config_path, right_config_path)
# atexit.register(cleanup)
# signal.signal(signal.SIGINT, signal_handler)
# signal.signal(signal.SIGTERM, signal_handler)

# Note: avoid CLI parsing when called from server context
bimanual = right_config_path is not None
logger.info("Bimanual mode: %s", bimanual)

# Load configs
logger.info("Loading left config from %s", left_config_path)
left_cfg = OmegaConf.to_container(OmegaConf.load(left_config_path), resolve=True)
if bimanual:
    logger.info("Loading right config from %s", right_config_path)
    right_cfg = OmegaConf.to_container(OmegaConf.load(right_config_path), resolve=True)

# Create agent
if bimanual:
    from gello.agents.agent import BimanualAgent

    logger.info("Instantiating BimanualAgent")
    agent = BimanualAgent(
        agent_left=None,
        agent_right=instantiate_from_dict(right_cfg["agent"]),
    )
else:
    logger.info("Instantiating single agent from left config")
    agent = instantiate_from_dict(left_cfg["agent"])

launched = True
logger.info("Robots launched")

def wait_for_server_ready(port, host="127.0.0.1", timeout_seconds=5):
    """Wait for ZMQ server to be ready with retry logic."""
    from gello.zmq_core.robot_node import ZMQClientRobot

    attempts = int(timeout_seconds * 10)  # 0.1s intervals
    logger.info("Waiting for server %s:%s (timeout=%ss, attempts=%s)", host, port, timeout_seconds, attempts)
    for attempt in range(attempts):
        try:
            logger.debug("Probe attempt %s/%s to %s:%s", attempt + 1, attempts, host, port)
            client = ZMQClientRobot(port=port, host=host)
            time.sleep(0.1)
            logger.info("Server ready on %s:%s", host, port)
            return True
        except (zmq.error.ZMQError, Exception):
            logger.debug("Server not ready; retrying...")
            time.sleep(0.1)
        finally:
            if "client" in locals():
                logger.debug("Closing probe client")
                client.close()
            time.sleep(0.1)
            if attempt == attempts - 1:
                msg = f"Server failed to start on {host}:{port} within {timeout_seconds} seconds"
                logger.error(msg)
                raise RuntimeError(msg)
    return False


@dataclass
class Args:
    left_config_path: str
    """Path to the left arm configuration YAML file."""

    right_config_path: Optional[str] = None
    """Path to the right arm configuration YAML file (for bimanual operation)."""

    use_save_interface: bool = False
    """Enable saving data with keyboard interface."""


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.warning("Received signal %s; performing cleanup and exiting", signum)
    cleanup()
    import os

    os._exit(0)


# def launch_robots():
    

def _intervention_loop() -> None:
    """Runs in a background thread; executes intervention and queued tasks."""
    global currently_intervening

    if not launched:
        logger.error("Intervention loop requested but robots not launched")
        return
    
    print("webrtc_intervene_endpoint: ", webrtc_intervene_endpoint)

    try:
        # Move to safe starting position with torque enabled
        logger.info("[intervention] Moving robots to starting position...")
        agent.agent_right.set_torque_mode(True)
        time.sleep(1)
        logger.debug("[intervention] Torque enabled on both agents")
        arr = -1 * np.array([7.79108842, 7.85551561, 3.11858294, 1.22565065, 4.58353459, 1.58613613, 2.95291302, 4.81056375, 4.72466083, 3.12471886, 5.85673865, 1.53091283, -1, 0])
        print(arr)
        # agent.agent_left.move_to_position(arr[:7])
        agent.agent_right.move_to_position(arr[7:])
        time.sleep(0.3)
        logger.debug("[intervention] Move to starting position issued")
        # time.sleep(5)

        # Enable gravity compensation - user can now move robots freely
        logger.info("[intervention] Enabling gravity compensation mode...")
        # agent.agent_left.set_torque_mode(False)
        agent.agent_right.set_torque_mode(False)
        logger.debug("[intervention] Torque disabled; gravity compensation active")

        currently_intervening = True
        logger.info("[intervention] Active - robots in gravity compensation mode")

        # Main intervention loop: execute queued tasks and lightweight monitoring
        counter = 0
        import urllib.request
        from urllib.error import URLError, HTTPError

        while (not intervention_stop_event.is_set()) and currently_intervening:
            joint_states = agent.act({})
            counter += 1
            if counter % 1000 == 0:
                print("currently intervening with joint states: ", joint_states)
            # Prefer WebRTC DataChannel if available; otherwise fallback to HTTP POST
            try:
                if webrtc_dc is not None and getattr(webrtc_dc, "readyState", "") == "open":
                    payload = {
                        "joint_states": joint_states.tolist() if hasattr(joint_states, 'tolist') else joint_states,
                        "timestamp": time.time(),
                    }
                    _webrtc_send_json(payload)
                elif webrtc_intervene_endpoint:
                    payload = json.dumps({"joint_states": joint_states.tolist() if hasattr(joint_states, 'tolist') else joint_states}).encode("utf-8")
                    req = urllib.request.Request(
                        webrtc_intervene_endpoint,
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=0.05) as _:
                        pass
            except (URLError, HTTPError, Exception):
                # Swallow transient errors to keep the control loop running
                pass

            time.sleep(0.01)

    except Exception as e:
        logger.exception("[intervention] Fatal error: %s", e)
    finally:
        # Restore safe state on exit
        try:
            agent.agent_left.set_torque_mode(True)
            agent.agent_right.set_torque_mode(True)
        except Exception:
            logger.debug("[intervention] Error while restoring torque mode (ignored)", exc_info=True)
        currently_intervening = False
        logger.info("[intervention] Stopped")


def start_intervention() -> bool:
    """Start the intervention thread. Returns True if started, False if already running."""
    global intervention_thread
    with intervention_lock:
        if intervention_thread is not None and intervention_thread.is_alive():
            logger.info("Intervention thread already running")
            return False
        intervention_stop_event.clear()
        logger.info("Starting intervention thread")

        intervention_thread = threading.Thread(target=_intervention_loop, name="intervention-thread", daemon=True)
        intervention_thread.start()
        active_threads.append(intervention_thread)
        logger.info("Intervention thread started: %s", intervention_thread.name)

        logger.info("Intervention thread started")
        return True


def stop_intervention() -> None:
    """Signal the intervention thread to stop and wait for termination."""
    global intervention_thread
    with intervention_lock:
        logger.info("Stopping intervention thread")
        # Signal loop to stop
        try:
            global currently_intervening
            currently_intervening = False
        except Exception:
            pass
        intervention_stop_event.set()
        if intervention_thread is not None and intervention_thread.is_alive():
            logger.debug("Joining intervention thread %s", intervention_thread.name)
            intervention_thread.join(timeout=2)
            if intervention_thread.is_alive():
                logger.warning("Intervention thread did not stop within timeout")
        intervention_thread = None
        logger.info("Intervention thread stopped")


def _ensure_webrtc_loop() -> None:
    """Ensure a dedicated asyncio loop is running for WebRTC operations."""
    global webrtc_loop, webrtc_loop_thread
    if webrtc_loop is not None and webrtc_loop_thread is not None and webrtc_loop_thread.is_alive():
        return
    webrtc_loop = asyncio.new_event_loop()
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()
    webrtc_loop_thread = threading.Thread(target=_run_loop, args=(webrtc_loop,), name="webrtc-loop", daemon=True)
    webrtc_loop_thread.start()


async def _webrtc_negotiate(endpoint: str) -> tuple[RTCPeerConnection, object]:
    """Create RTCPeerConnection, DataChannel, negotiate with server, and return (pc, dc)."""
    pc = RTCPeerConnection()
    dc = pc.createDataChannel("control")

    def _on_state_change() -> None:
        logger.info("[webrtc] connection state: %s", getattr(pc, "connectionState", None))

    @pc.on("connectionstatechange")
    async def on_connectionstatechange() -> None:
        _on_state_change()

    # Register DataChannel events
    global webrtc_connected
    @dc.on("open")
    def _on_open() -> None:
        webrtc_connected = True
        logger.info("[webrtc] DataChannel open")

    @dc.on("close")
    def _on_close() -> None:
        webrtc_connected = False
        logger.info("[webrtc] DataChannel closed")

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Send offer to server and get answer
    import urllib.request
    req = urllib.request.Request(
        endpoint,
        data=json.dumps({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=2.0) as resp:
        answer_bytes = resp.read()
        answer = json.loads(answer_bytes.decode("utf-8"))
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))

    return pc, dc


def start_webrtc_connection() -> None:
    """Start WebRTC negotiation with the configured endpoint (if any)."""
    global webrtc_pc, webrtc_dc, webrtc_connected
    if not webrtc_intervene_endpoint:
        return
    try:
        _ensure_webrtc_loop()
        future = asyncio.run_coroutine_threadsafe(
            _webrtc_negotiate(webrtc_intervene_endpoint), webrtc_loop
        )
        pc, dc = future.result(timeout=5.0)
        webrtc_pc = pc
        webrtc_dc = dc
        webrtc_connected = True
        logger.info("[webrtc] DataChannel created and negotiation complete")
    except Exception as e:
        logger.exception("[webrtc] negotiation failed: %s", e)
        webrtc_connected = False


def stop_webrtc_connection() -> None:
    """Close WebRTC connection and stop loop if idle."""
    global webrtc_pc, webrtc_dc, webrtc_connected
    try:
        if webrtc_dc is not None:
            try:
                webrtc_dc.close()
            except Exception:
                pass
        if webrtc_pc is not None:
            fut = asyncio.run_coroutine_threadsafe(webrtc_pc.close(), webrtc_loop) if webrtc_loop else None
            if fut is not None:
                try:
                    fut.result(timeout=2.0)
                except Exception:
                    pass
    finally:
        webrtc_pc = None
        webrtc_dc = None
        webrtc_connected = False


def enqueue_intervention_task(task_callable, *args, **kwargs) -> None:
    """Enqueue a callable to run inside the intervention thread.

    The callable will be invoked as task_callable(agent, *args, **kwargs).
    """
    try:
        task_name = getattr(task_callable, "__name__", repr(task_callable))
    except Exception:
        task_name = repr(task_callable)
    logger.info("Queueing intervention task: %s", task_name)
    intervention_tasks.put((task_callable, args, kwargs))


def is_intervention_active() -> bool:
    return (intervention_thread is not None and intervention_thread.is_alive())


def intervene():
    """Backward-compatible shim: start intervention thread."""
    started = start_intervention()
    if started:
        logger.info("Intervention thread started")
    else:
        logger.info("Intervention already running")


def stop_intervene():
    """Backward-compatible shim: stop intervention thread."""
    logger.info("Stopping intervention...")
    stop_intervention()


# -------------------- Flask App (same file) --------------------
app = Flask(__name__)
CORS(app)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/intervene")
def api_intervene_status():
    return jsonify({"active": bool(is_intervention_active())})


@app.post("/intervene")
def api_intervene_start():
    global webrtc_intervene_endpoint, curr_tracking_robot_webrtc
    if not launched:
        return jsonify({"status": "not_launched", "message": "Robots not launched"}), 400
    
    if is_intervention_active():
        return jsonify({"status": "already_active"}), 200

    # Accept optional streamAddress from UI and build target endpoint
    try:
        body = request.get_json(silent=True) or {}
        stream_address = (body.get("streamAddress") or "").strip()
    except Exception:
        stream_address = ""

    print("stream_address: ", stream_address)

    if stream_address:
        try:
            from urllib.parse import urlparse, urlunparse

            parsed = urlparse(stream_address if "://" in stream_address else f"http://{stream_address}")
            base = parsed._replace(path="/client/intervene", params="", query="", fragment="")
            print("base: ", base)
            webrtc_intervene_endpoint = urlunparse(base)
            print("webrtc_intervene_endpoint: ", webrtc_intervene_endpoint)
            curr_tracking_robot_webrtc = stream_address
            # Attempt WebRTC negotiation up-front for low-latency channel
            try:
                start_webrtc_connection()
            except Exception:
                logger.debug("[webrtc] failed to start (continuing with HTTP fallback)", exc_info=True)
        except Exception:
            # If invalid, ignore and proceed without endpoint
            webrtc_intervene_endpoint = None
            curr_tracking_robot_webrtc = None
    else:
        webrtc_intervene_endpoint = None
        curr_tracking_robot_webrtc = None

    intervene()
    # Race: became active between checks
    return jsonify({"status": "already_active"}), 200


@app.delete("/intervene")
def api_intervene_stop():
    if not is_intervention_active():
        return jsonify({"status": "not_active"}), 400
    # Close WebRTC channel if open
    try:
        stop_webrtc_connection()
    except Exception:
        logger.debug("[webrtc] error stopping connection", exc_info=True)
    stop_intervention()
    return jsonify({"status": "stopped", "message": "Intervention mode ended"})


@app.post("/launch")
def api_launch():
    """Launch robots if not already launched."""
    global launched
    if launched:
        return jsonify({"status": "already_launched"}), 200

    try:
        # Run launch synchronously on the main thread
        # launch_robots()
        return jsonify({"status": "launched"}), 200
    except Exception as e:
        logger.exception("Launch failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Run single-threaded so request handlers execute on the main thread
    app.run(host="0.0.0.0", port=5001, debug=False, threaded=False)
