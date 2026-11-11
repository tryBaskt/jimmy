import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './style.css';
import { hasSession, initialize, login, signup } from './client';

const root = createRoot(document.getElementById('root')!);
initialize().then(() => {
  root.render(<React.StrictMode>{hasSession() ? <App /> : <main className="empty">
    <h1>Jimmy Research</h1><p>Create an account or sign in to replicate a paper and inspect the evidence.</p>
    <div className="auth-actions">
      <button className="primary" onClick={() => signup().catch(showError)}>Create account</button>
      <button className="secondary" onClick={() => login().catch(showError)}>Sign in</button>
    </div>
  </main>}</React.StrictMode>);
}).catch(showError);
function showError(error: Error) {
  root.render(<main className="empty"><h1>Unable to continue</h1><p role="alert">{error.message}</p>
    <a href="/">Try again</a></main>);
}
