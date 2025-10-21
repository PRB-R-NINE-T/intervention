import atexit
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
from flask import Flask, jsonify
from flask_cors import CORS

# Global variable to store agent instance
agent = None
launched = False
curr_tracking_robot_webrtc = None
currently_intervening = False

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
        while currently_intervening:
            joint_states = agent.act({})
            counter += 1
            if counter % 100 == 0:
                print("currently intervening with joint states: ", joint_states)
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
        intervention_stop_event.set()
        if intervention_thread is not None and intervention_thread.is_alive():
            logger.debug("Joining intervention thread %s", intervention_thread.name)
            intervention_thread.join(timeout=1)
        intervention_thread = None
        logger.info("Intervention thread stopped")


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
    if not launched:
        return jsonify({"status": "not_launched", "message": "Robots not launched"}), 400
    
    if is_intervention_active():
        return jsonify({"status": "already_active"}), 200

    intervene()
    # Race: became active between checks
    return jsonify({"status": "already_active"}), 200


@app.delete("/intervene")
def api_intervene_stop():
    if not is_intervention_active():
        return jsonify({"status": "not_active"}), 400
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
