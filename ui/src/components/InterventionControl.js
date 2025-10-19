import React, { useState, useEffect } from 'react';
import './InterventionControl.css';

function InterventionControl({ isActive, onStart, onStop, streamAddress }) {
  const [countdown, setCountdown] = useState(null);
  const [showMessage, setShowMessage] = useState(false);
  const [error, setError] = useState(null);

  // API base is proxied by CRA dev server (3000 -> 8080), use relative paths
  const apiFetch = (path, init) => fetch(path, init);

  const parseResponse = async (response) => {
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      try {
        const data = await response.json();
        if (!response.ok) {
          const message = (data && data.message) || JSON.stringify(data);
          throw new Error(message || `Request failed (${response.status})`);
        }
        return data;
      } catch (e) {
        // If declared JSON but parsing failed, fall back to text
        const text = await response.text().catch(() => '');
        throw new Error(text || (e && e.message) || 'Invalid JSON response');
      }
    } else {
      const text = await response.text().catch(() => '');
      if (!response.ok) {
        throw new Error(text || `Request failed (${response.status})`);
      }
      throw new Error(text || 'Unexpected non-JSON response');
    }
  };

  useEffect(() => {
    if (countdown === null) return;

    if (countdown > 0) {
      const timer = setTimeout(() => {
        setCountdown(countdown - 1);
      }, 1000);
      return () => clearTimeout(timer);
    } else {
      // Countdown finished, show the message
      setShowMessage(true);
    }
  }, [countdown]);

  const handleStartClick = async () => {
    setError(null);
    setCountdown(5);
    
    try {
      const response = await apiFetch(`/intervene`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}'
      });
      const data = await parseResponse(response);
      if (data.status === 'started' || data.status === 'already_active') {
        onStart();
      } else {
        setError('Failed to start intervention');
        setCountdown(null);
      }
    } catch (err) {
      setError(`Error: ${err.message}`);
      setCountdown(null);
    }
  };

  const handleStopClick = async () => {
    setError(null);
    
    try {
      const response = await apiFetch(`/intervene`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: '{}'
      });
      const data = await parseResponse(response);
      if (data.status === 'stopped' || data.status === 'not_active') {
        setCountdown(null);
        setShowMessage(false);
        onStop();
      } else {
        setError('Failed to stop intervention');
      }
    } catch (err) {
      setError(`Error: ${err.message}`);
    }
  };

  return (
    <div className="intervention-control">
      <div className="control-card">
        <h2>Intervention Control</h2>
        
        {error && (
          <div className="error-message" style={{
            color: '#ff4444',
            padding: '10px',
            marginBottom: '10px',
            borderRadius: '5px',
            background: 'rgba(255, 68, 68, 0.1)',
            fontWeight: 'bold'
          }}>
            {error}
          </div>
        )}
        
        {!isActive ? (
          <button 
            className="btn btn-start"
            onClick={handleStartClick}
          >
            <svg 
              width="24" 
              height="24" 
              viewBox="0 0 24 24" 
              fill="none" 
              stroke="currentColor" 
              strokeWidth="2"
            >
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
            Take over
          </button>
        ) : (
          <>
            {countdown !== null && countdown > 0 && (
              <div className="countdown-display">
                <div className="countdown-number">{countdown}</div>
                <p className="countdown-label">Starting in...</p>
              </div>
            )}
            
            {showMessage && (
              <div className="intervention-message">
                <div className="message-icon">
                  <svg 
                    width="48" 
                    height="48" 
                    viewBox="0 0 24 24" 
                    fill="none" 
                    stroke="currentColor" 
                    strokeWidth="2"
                  >
                    <path d="M12 2L2 7l10 5 10-5-10-5z" />
                    <path d="M2 17l10 5 10-5" />
                    <path d="M2 12l10 5 10-5" />
                  </svg>
                </div>
                <h3>You now have control over the robot. Take over</h3>
                <div className="status-badge">
                  <span className="pulse-dot"></span>
                  Active
                </div>
              </div>
            )}
            
            <button 
              className="btn btn-stop"
              onClick={handleStopClick}
            >
              <svg 
                width="24" 
                height="24" 
                viewBox="0 0 24 24" 
                fill="none" 
                stroke="currentColor" 
                strokeWidth="2"
              >
                <rect x="6" y="6" width="12" height="12" />
              </svg>
              Stop Intervention
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export default InterventionControl;

