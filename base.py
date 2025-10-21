import asyncio
import json
import logging

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription


routes = web.RouteTableDef()
_pcs: set[RTCPeerConnection] = set()


def _attach_logging_handlers(peer_connection: RTCPeerConnection) -> None:
    @peer_connection.on("datachannel")
    async def on_datachannel(channel) -> None:
        logging.info("DataChannel created: %s", channel.label)

        @channel.on("message")
        def on_message(message) -> None:
            try:
                if isinstance(message, (bytes, bytearray)):
                    message = message.decode("utf-8", errors="ignore")
                payload = json.loads(message)
                if isinstance(payload, dict) and "joint_states" in payload:
                    # Compute one-way latency if sender included timestamp
                    sent_ts = payload.get("timestamp")
                    if isinstance(sent_ts, (int, float)):
                        import time
                        now = time.time()
                        latency_ms = (now - float(sent_ts)) * 1000.0
                        logging.info("Received joint_states via DataChannel (len=%s) latency=%.2fms",
                                     len(payload.get("joint_states") or []), latency_ms)
                    else:
                        logging.info("Received joint_states via DataChannel (len=%s)",
                                     len(payload.get("joint_states") or []))
                else:
                    logging.info("DataChannel %s message: %s", channel.label, payload)
            except Exception:
                logging.info("DataChannel %s non-JSON message: %s", channel.label, message)

    @peer_connection.on("track")
    async def on_track(track) -> None:
        logging.info("Track received: kind=%s", track.kind)

        async def log_frames() -> None:
            try:
                while True:
                    frame = await track.recv()
                    if track.kind == "video":
                        logging.info(
                            "Video frame: %sx%s pts=%s time_base=%s",
                            getattr(frame, "width", None),
                            getattr(frame, "height", None),
                            getattr(frame, "pts", None),
                            getattr(frame, "time_base", None),
                        )
                    elif track.kind == "audio":
                        logging.info(
                            "Audio frame: samples=%s layout=%s sample_rate=%s pts=%s",
                            getattr(frame, "samples", None),
                            getattr(frame, "layout", None),
                            getattr(frame, "sample_rate", None),
                            getattr(frame, "pts", None),
                        )
            except Exception as exc:
                logging.info("Track %s ended: %s", track.kind, exc)

        asyncio.create_task(log_frames())


@routes.post("/client/intervene")
async def intervene(request: web.Request) -> web.Response:
    body = await request.text()
    logging.info("/client/intervene raw body: %s", body)

    # Try to parse JSON payload, but tolerate non-JSON bodies
    try:
        data = json.loads(body) if body else {}
    except Exception as exc:
        logging.info("/client/intervene non-JSON body: %s", exc)
        data = {}

    # If this looks like a WebRTC offer, handle SDP negotiation
    if isinstance(data, dict) and "sdp" in data and "type" in data:
        try:
            offer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
        except Exception as exc:
            logging.exception("Invalid SDP payload: %s", exc)
            return web.json_response({"error": "invalid sdp payload"}, status=400)

        pc = RTCPeerConnection()
        _pcs.add(pc)
        _attach_logging_handlers(pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            logging.info("PeerConnection state: %s", pc.connectionState)
            if pc.connectionState in ("closed", "failed", "disconnected"):
                try:
                    await pc.close()
                finally:
                    _pcs.discard(pc)

        try:
            await pc.setRemoteDescription(offer)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
        except Exception as exc:
            logging.exception("Failed to handle SDP: %s", exc)
            await pc.close()
            return web.json_response({"error": "sdp negotiation failed"}, status=500)

        response = {
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
        }
        return web.json_response(response)

    # Otherwise, accept and log arbitrary JSON (e.g., joint states) and return 200
    logging.info("/client/intervene received non-SDP payload; acknowledging")
    return web.json_response({"status": "ok"})


def create_app() -> web.Application:
    app = web.Application()
    app.add_routes(routes)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(), port=8080)


