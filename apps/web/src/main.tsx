import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './style.css';
import { hasSession, initialize, login } from './client';

const root = createRoot(document.getElementById('root')!);
initialize().then(() => {
  root.render(<React.StrictMode>{hasSession() ? <App /> : <main className="empty">
    <h1>Jimmy Research</h1><p>Sign in to replicate a paper and inspect the evidence.</p>
    <button className="primary" onClick={() => login().catch(showError)}>Sign in</button>
  </main>}</React.StrictMode>);
}).catch(showError);
function showError(error: Error) {
  root.render(<main className="empty"><h1>Unable to sign in</h1><p role="alert">{error.message}</p>
    <a href="/">Try again</a></main>);
}
