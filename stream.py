#!/usr/bin/env python3
"""
iPhone Video Capture and WebRTC Streaming Server

This script captures video from an iPhone camera and streams it via WebRTC.
To use with iPhone:
1. Install an app like "EpocCam" or "iVCam" on your iPhone, OR
2. Use iPhone as IP camera with an app like "IP Webcam" or "DroidCam", OR
3. Connect iPhone via USB with continuity camera (macOS)
"""

import asyncio
import cv2
import json
import logging
import numpy as np
import time
from concurrent.futures import ThreadPoolExecutor
from aiohttp import web
from aiohttp_cors import setup as cors_setup, ResourceOptions
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaRelay
from av import VideoFrame
import socket

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables
relay = MediaRelay()
pcs = set()
camera_source = None

# Intervention control
intervention_active = False
intervention_task = None
executor = ThreadPoolExecutor(max_workers=2)


class CameraVideoTrack(VideoStreamTrack):
    """
    A video track that captures frames from OpenCV camera source
    """
    
    def __init__(self, camera_index=0):
        super().__init__()
        self.camera_index = camera_index
        self.cap = None
        self._initialize_camera()
    
    def _initialize_camera(self):
        """Initialize camera capture"""
        # Try different camera indices for iPhone
        camera_options = [self.camera_index, 1, 2, 0]
        
        for idx in camera_options:
            logger.info(f"Trying to open camera index: {idx}")
            self.cap = cv2.VideoCapture(idx)
            
            # Try to set higher resolution for iPhone cameras
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            
            if self.cap.isOpened():
                ret, frame = self.cap.read()
                if ret:
                    logger.info(f"✓ Successfully opened camera at index {idx}")
                    logger.info(f"  Resolution: {int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}")
                    self.camera_index = idx
                    return
                else:
                    self.cap.release()
            
        # If all else fails, try default camera
        logger.warning("Could not find iPhone camera, using default camera")
        self.cap = cv2.VideoCapture(0)
        
        if not self.cap.isOpened():
            raise RuntimeError("Could not open any camera. Please ensure your iPhone is connected and recognized as a camera.")
    
    async def recv(self):
        """
        Receive the next video frame
        """
        pts, time_base = await self.next_timestamp()
        
        ret, frame = self.cap.read()
        if not ret:
            logger.error("Failed to capture frame")
            # Try to reinitialize camera
            self._initialize_camera()
            ret, frame = self.cap.read()
            if not ret:
                # Return a blank frame if still failing
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
        
        # Convert BGR to RGB (OpenCV uses BGR, WebRTC uses RGB)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Create VideoFrame from numpy array
        video_frame = VideoFrame.from_ndarray(frame, format="rgb24")
        video_frame.pts = pts
        video_frame.time_base = time_base
        
        return video_frame
    
    def stop(self):
        """Release the camera"""
        if self.cap:
            self.cap.release()
            logger.info("Camera released")


async def index(request):
    """Serve the main HTML page"""
    html_content = """
<!DOCTYPE html>
<html>
<head>
    <title>iPhone WebRTC Stream</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .container {
            background: rgba(255, 255, 255, 0.1);
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
            backdrop-filter: blur(4px);
            border: 1px solid rgba(255, 255, 255, 0.18);
            max-width: 90%;
        }
        h1 {
            margin-top: 0;
            text-align: center;
        }
        #videoElement {
            width: 100%;
            max-width: 1280px;
            height: auto;
            border-radius: 10px;
            background: #000;
            display: block;
            margin: 20px auto;
        }
        .controls {
            text-align: center;
            margin-top: 20px;
        }
        button {
            background: white;
            color: #667eea;
            border: none;
            padding: 12px 30px;
            font-size: 16px;
            border-radius: 25px;
            cursor: pointer;
            margin: 5px;
            font-weight: bold;
            transition: all 0.3s;
        }
        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.3);
        }
        button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        .status {
            text-align: center;
            margin: 15px 0;
            padding: 10px;
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.2);
        }
        .status.connected {
            background: rgba(76, 175, 80, 0.3);
        }
        .status.error {
            background: rgba(244, 67, 54, 0.3);
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📱 iPhone WebRTC Stream</h1>
        <div class="status" id="status">Disconnected</div>
        <video id="videoElement" autoplay playsinline></video>
        <div class="controls">
            <button id="startButton" onclick="start()">Start Stream</button>
            <button id="stopButton" onclick="stop()" disabled>Stop Stream</button>
        </div>
    </div>

    <script>
        var pc = null;
        var videoElement = document.getElementById('videoElement');
        var startButton = document.getElementById('startButton');
        var stopButton = document.getElementById('stopButton');
        var statusElement = document.getElementById('status');

        function updateStatus(message, type = '') {
            statusElement.textContent = message;
            statusElement.className = 'status ' + type;
        }

        function negotiate() {
            return pc.createOffer().then(function(offer) {
                return pc.setLocalDescription(offer);
            }).then(function() {
                // Wait for ICE gathering to complete
                return new Promise(function(resolve) {
                    if (pc.iceGatheringState === 'complete') {
                        resolve();
                    } else {
                        function checkState() {
                            if (pc.iceGatheringState === 'complete') {
                                pc.removeEventListener('icegatheringstatechange', checkState);
                                resolve();
                            }
                        }
                        pc.addEventListener('icegatheringstatechange', checkState);
                    }
                });
            }).then(function() {
                var offer = pc.localDescription;
                return fetch('/offer', {
                    body: JSON.stringify({
                        sdp: offer.sdp,
                        type: offer.type,
                    }),
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    method: 'POST'
                });
            }).then(function(response) {
                return response.json();
            }).then(function(answer) {
                return pc.setRemoteDescription(answer);
            }).catch(function(e) {
                updateStatus('Error: ' + e, 'error');
                console.error(e);
            });
        }

        function start() {
            updateStatus('Connecting...', '');
            startButton.disabled = true;

            var config = {
                sdpSemantics: 'unified-plan'
            };

            pc = new RTCPeerConnection(config);

            // Connect audio / video
            pc.addEventListener('track', function(evt) {
                updateStatus('Connected - Streaming', 'connected');
                videoElement.srcObject = evt.streams[0];
                stopButton.disabled = false;
            });

            pc.addEventListener('connectionstatechange', function() {
                console.log('Connection state:', pc.connectionState);
                if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
                    updateStatus('Connection ' + pc.connectionState, 'error');
                    stop();
                }
            });

            // Request video
            pc.addTransceiver('video', {direction: 'recvonly'});

            return negotiate();
        }

        function stop() {
            stopButton.disabled = true;
            
            // Close peer connection
            if (pc) {
                pc.close();
                pc = null;
            }

            // Stop video
            if (videoElement.srcObject) {
                videoElement.srcObject.getTracks().forEach(track => track.stop());
                videoElement.srcObject = null;
            }

            updateStatus('Disconnected', '');
            startButton.disabled = false;
        }
    </script>
</body>
</html>
    """
    return web.Response(content_type="text/html", text=html_content)


async def offer(request):
    """Handle WebRTC offer from client"""
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"Connection state: {pc.connectionState}")
        if pc.connectionState == "failed" or pc.connectionState == "closed":
            await pc.close()
            pcs.discard(pc)

    # Create video track
    video_track = CameraVideoTrack(camera_index=0)
    pc.addTrack(video_track)

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type="application/json",
        text=json.dumps(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
        ),
    )


# Delegate to run_robots thread-based system instead of managing our own loop
def _get_robot_module():
    import sys
    sys.path.insert(0, '/Users/pierre/Desktop/intervention')
    from agent.experiments import run_robots as rr
    return rr


async def start_intervention(request):
    """Start intervention mode"""
    rr = _get_robot_module()
    if not rr.launched:
        return web.json_response({"status": "not_launched", "message": "Robots not launched"}, status=400)

    started = rr.start_intervention()
    if started:
        logger.info("Intervention started")
        return web.json_response({"status": "started", "message": "Intervention mode active"})
    else:
        return web.json_response({"status": "already_active"})


async def stop_intervention(request):
    """Stop intervention mode"""
    rr = _get_robot_module()
    if not rr.is_intervention_active():
        return web.json_response({"status": "not_active"})

    rr.stop_intervention()
    logger.info("Intervention stopped")
    return web.json_response({"status": "stopped", "message": "Intervention mode ended"})


async def intervention_status(request):
    """Get current intervention status"""
    rr = _get_robot_module()
    return web.json_response({"active": rr.is_intervention_active()})


async def on_shutdown(app):
    """Cleanup on shutdown"""
    # Ensure intervention thread is stopped
    rr = _get_robot_module()
    if rr.is_intervention_active():
        rr.stop_intervention()
    
    # Close all peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()


def get_local_ip():
    """Get the local IP address"""
    try:
        # Create a socket to get the local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "localhost"


async def main():
    """Main function to run the server"""
    async def cors_middleware(app, handler):
        async def middleware_handler(request):
            # Handle preflight
            if request.method == 'OPTIONS':
                headers = {
                    'Access-Control-Allow-Origin': request.headers.get('Origin', '*'),
                    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
                    'Access-Control-Allow-Headers': request.headers.get('Access-Control-Request-Headers', '*'),
                    'Access-Control-Allow-Credentials': 'true',
                }
                return web.Response(status=204, headers=headers)

            response = await handler(request)
            response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            return response
        return middleware_handler

    app = web.Application(middlewares=[cors_middleware])
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    
    # Intervention control endpoints
    app.router.add_post("/intervention/start", start_intervention)
    app.router.add_post("/intervention/stop", stop_intervention)
    app.router.add_get("/intervention/status", intervention_status)

    # Setup CORS for routes (in addition to middleware) to satisfy various clients
    cors = cors_setup(app, defaults={
        "*": ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*"
        )
    })
    for route in list(app.router.routes()):
        cors.add(route)

    port = 8080
    local_ip = get_local_ip()
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print("\n" + "="*60)
    print("🎥 iPhone WebRTC Stream Server Started!")
    print("="*60)
    print(f"\n📱 Connect your iPhone as a camera:")
    print("   - On macOS Ventura+: Use Continuity Camera (iPhone will appear as camera)")
    print("   - Alternative: Install apps like EpocCam, iVCam, or similar")
    print("\n🌐 Access the stream at:")
    print(f"   Local:   http://localhost:{port}")
    print(f"   Network: http://{local_ip}:{port}")
    print("\n💡 Open the URL in your browser and click 'Start Stream'")
    print("="*60 + "\n")

    # Keep the server running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("\nShutting down server...")
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

