import React, { useState, useRef } from 'react';
import './App.css';
import VideoStream from './components/VideoStream';
import InterventionControl from './components/InterventionControl';

function App() {
  const [streamAddress, setStreamAddress] = useState('');
  const [robotAddress, setRobotAddress] = useState('');
  const [isInterventionActive, setIsInterventionActive] = useState(false);
  const [isStreamConnected, setIsStreamConnected] = useState(false);
  const [isStreamConnecting, setIsStreamConnecting] = useState(false);
  const streamControlsRef = useRef(null);

  return (
    <div className="App">
      <header className="app-header">
        <h1>Robot Intervention Interface</h1>
      </header>
      
      <main className="app-main">
        <div className="stream-input-group">
          <label htmlFor="stream-address">WebRTC Stream Address:</label>
          <div className="stream-input-row">
            <input
              id="stream-address"
              type="text"
              placeholder="Enter WebRTC stream address"
              value={streamAddress}
              onChange={(e) => setStreamAddress(e.target.value)}
              className="stream-input"
            />
            <button 
              onClick={() => streamControlsRef.current?.startStream()} 
              disabled={isStreamConnected || isStreamConnecting || !streamAddress}
              className="btn btn-primary btn-start-stream"
            >
              Start Stream
            </button>
          </div>
        </div>
        
        <div className="main-content">
          <div className="stream-section">
            <VideoStream 
              ref={streamControlsRef}
              streamAddress={streamAddress}
              onConnectionChange={(connected, connecting) => {
                setIsStreamConnected(connected);
                setIsStreamConnecting(connecting);
              }}
            />
          </div>
          <div className="side-panel">
            <InterventionControl
              isActive={isInterventionActive}
              onStart={() => setIsInterventionActive(true)}
              onStop={() => setIsInterventionActive(false)}
              streamAddress={streamAddress}
              robotAddress={robotAddress}
              onRobotAddressChange={setRobotAddress}
            />
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;

