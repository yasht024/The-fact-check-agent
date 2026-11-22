import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

function App() {
  return (
    <main>
      <h1>Fact-Checking Web App</h1>
      <p>PDF uploads and source-backed fact-check reports are planned for later phases.</p>
    </main>
  );
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
