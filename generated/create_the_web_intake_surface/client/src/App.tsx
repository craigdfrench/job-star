// client/src/App.tsx
import { IntakeForm } from "./components/IntakeForm";

export default function App() {
  return (
    <div className="app-shell">
      <main className="app-main">
        <IntakeForm />
      </main>
    </div>
  );
}


// --- DUPLICATE BLOCK ---

// client/src/App.tsx
import { useState } from 'react';
import { IntakeForm } from './components/IntakeForm';
import { IntakeList } from './components/IntakeList';
import { IntakeDetail } from './components/IntakeDetail';

type View =
  | { name: 'form' }
  | { name: 'list' }
  | { name: 'detail'; intakeId: string };

export default function App() {
  const [view, setView] = useState<View>({ name: 'form' });

  return (
    <div className="app">
      <header className="app-header">
        <h1>Job-Star</h1>
        <nav className="app-nav">
          <button
            className={view.name === 'form' ? 'nav-active' : ''}
            onClick={() => setView({ name: 'form' })}
          >
            New Intake
          </button>
          <button
            className={view.name === 'list' || view.name === 'detail' ? 'nav-active' : ''}
            onClick={() => setView({ name: 'list' })}
          >
            Intakes
          </button>
        </nav>
      </header>

      <main className="app-main">
        {view.name === 'form' && (
          <IntakeForm
            onSubmitted={(id) => setView({ name: 'detail', intakeId: id })}
          />
        )}

        {view.name === 'list' && (
          <IntakeList onSelect={(id) => setView({ name: 'detail', intakeId: id })} />
        )}

        {view.name === 'detail' && (
          <IntakeDetail
            intakeId={view.intakeId}
            onBack={() => setView({ name: 'list' })}
          />
        )}
      </main>
    </div>
  );
}
