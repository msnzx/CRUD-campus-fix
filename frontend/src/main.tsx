import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { AuthProvider } from './auth/AuthProvider'
import App from './App'
import { prefetchLookups } from './lib/useLookups'
import './index.css'

// Warm the reference-data cache while Supabase Auth is still resolving the
// session, so the two round trips overlap instead of running back to back.
prefetchLookups()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
