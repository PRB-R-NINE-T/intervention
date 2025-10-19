import React, { useRef, useState, useImperativeHandle, forwardRef, useEffect } from 'react';
import './VideoStream.css';

const VideoStream = forwardRef(({ streamAddress, onConnectionChange }, ref) => {
  const videoRef = useRef(null);
  const pcRef = useRef(null);
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState(null);

  // Notify parent of connection state changes
  useEffect(() => {
    if (onConnectionChange) {
      onConnectionChange(isConnected, isConnecting);
    }
  }, [isConnected, isConnecting, onConnectionChange]);

  const startStream = async () => {
    if (!streamAddress) {
      setError('Please enter a stream address');
      return;
    }

    try {
      setError(null);
      setIsConnecting(true);
      
      // Clean up any existing connection
      if (pcRef.current) {
        pcRef.current.close();
      }
      
      // Create a new RTCPeerConnection
      const pc = new RTCPeerConnection({
        sdpSemantics: 'unified-plan'
      });
      
      pcRef.current = pc;

      // Handle incoming streams
      pc.ontrack = (event) => {
        console.log('Received track:', event);
        if (videoRef.current && event.streams && event.streams[0]) {
          videoRef.current.srcObject = event.streams[0];
          setIsConnected(true);
          setIsConnecting(false);
        }
      };

      // Handle connection state changes
      pc.onconnectionstatechange = () => {
        console.log('Connection state:', pc.connectionState);
        if (pc.connectionState === 'connected') {
          setIsConnected(true);
          setIsConnecting(false);
        } else if (pc.connectionState === 'disconnected' || 
                   pc.connectionState === 'failed' || 
                   pc.connectionState === 'closed') {
          setIsConnected(false);
          setIsConnecting(false);
          if (pc.connectionState === 'failed') {
            setError('Connection failed');
          }
        }
      };

      // Add transceiver to receive video
      pc.addTransceiver('video', { direction: 'recvonly' });

      // Create and set local description (offer)
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      // Wait for ICE gathering to complete
      await new Promise((resolve) => {
        if (pc.iceGatheringState === 'complete') {
          resolve();
        } else {
          const checkState = () => {
            if (pc.iceGatheringState === 'complete') {
              pc.removeEventListener('icegatheringstatechange', checkState);
              resolve();
            }
          };
          pc.addEventListener('icegatheringstatechange', checkState);
        }
      });

      // Send offer to server and get answer
      const normalized = (() => {
        try {
          return new URL(streamAddress);
        } catch {
          return new URL(`http://${streamAddress}`);
        }
      })();
      const baseUrl = `${normalized.protocol}//${normalized.host}`;
      const response = await fetch(`${baseUrl}/offer`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          sdp: pc.localDescription.sdp,
          type: pc.localDescription.type
        })
      });

      if (!response.ok) {
        throw new Error(`Server responded with ${response.status}`);
      }

      const answer = await response.json();
      
      // Set remote description (answer from server)
      await pc.setRemoteDescription(new RTCSessionDescription(answer));
      
      console.log('WebRTC connection established');
      
    } catch (err) {
      console.error('Error connecting to stream:', err);
      setError(err.message);
      setIsConnected(false);
      setIsConnecting(false);
      if (pcRef.current) {
        pcRef.current.close();
        pcRef.current = null;
      }
    }
  };

  const stopStream = () => {
    if (pcRef.current) {
      pcRef.current.close();
      pcRef.current = null;
    }
    if (videoRef.current && videoRef.current.srcObject) {
      videoRef.current.srcObject.getTracks().forEach(track => track.stop());
      videoRef.current.srcObject = null;
    }
    setIsConnected(false);
    setIsConnecting(false);
    setError(null);
  };

  // Expose methods to parent via ref
  useImperativeHandle(ref, () => ({
    startStream,
    stopStream
  }));

  return (
    <div className="video-stream-container">
      <div className="video-wrapper">
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          className="video-element"
        />
        {!isConnected && !isConnecting && (
          <div className="video-placeholder">
            <div className="placeholder-content">
              <svg 
                width="64" 
                height="64" 
                viewBox="0 0 24 24" 
                fill="none" 
                stroke="currentColor" 
                strokeWidth="2"
              >
                <path d="M23 7l-7 5 7 5V7z" />
                <rect x="1" y="5" width="15" height="14" rx="2" ry="2" />
              </svg>
              <p>{streamAddress ? 'Click "Start Stream" above to begin' : 'Enter a WebRTC stream address above'}</p>
            </div>
          </div>
        )}
        {isConnecting && (
          <div className="video-overlay">
            <div className="loading-spinner"></div>
            <p>Connecting to stream...</p>
          </div>
        )}
        {error && (
          <div className="video-overlay error">
            <svg 
              width="48" 
              height="48" 
              viewBox="0 0 24 24" 
              fill="none" 
              stroke="currentColor" 
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <p>Error: {error}</p>
            <p className="error-hint">Check the stream address and try again</p>
          </div>
        )}
      </div>
      <div className="stream-controls">
        <div className="stream-status">
          <span className={`status-indicator ${isConnected ? 'connected' : 'disconnected'}`}></span>
          <span className="status-text">
            {isConnected ? 'Connected' : isConnecting ? 'Connecting...' : 'Disconnected'}
          </span>
        </div>
        <div className="stream-buttons">
          <button 
            onClick={stopStream} 
            disabled={!isConnected && !isConnecting}
            className="btn btn-secondary"
          >
            Stop Stream
          </button>
        </div>
      </div>
    </div>
  );
});

export default VideoStream;

