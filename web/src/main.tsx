import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { configureAuth } from './config'
import './index.css'

configureAuth()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
