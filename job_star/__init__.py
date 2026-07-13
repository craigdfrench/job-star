"""Job-Star: Constrained, supervised, goal-oriented AI orchestration.

A system that manages both coding and personal goals over extended timeframes.
It accepts raw input, triages it with AI, registers it in a shared goal
registry, and routes execution to the appropriate AI model.

Core loop:
    Intake → Context Gather → Triage → Conflict Check → Goal Registry
    → Router → Supervisor → AI Provider → Result → Follow-up
"""

__version__ = "0.1.0"