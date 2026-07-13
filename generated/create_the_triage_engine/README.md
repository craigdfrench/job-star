# Job-Star Triage Engine

Classifies incoming intake requests by **domain**, **urgency**, and **type**,
then checks for duplicates against the goal registry.

## Quick Start

### Install


// --- DUPLICATE BLOCK ---

# Classify a request
jobstar-triage classify --title "Fix crash in API endpoint" --description "Throws exception"

# Full triage with duplicate checking (seed registry from file)
jobstar-triage triage --title "Fix crash in API endpoint" --description "Throws exception" --registry-file goals.json

# Start the HTTP API server
jobstar-triage serve --port 8100


// --- DUPLICATE BLOCK ---

# Job-Star Triage Engine

Classifies incoming intake requests by **domain**, **urgency**, and **type**,
then checks for duplicates against the goal registry.

## Quick Start

### Install


// --- DUPLICATE BLOCK ---

# Classify a request
jobstar-triage classify --title "Fix crash in API endpoint" --description "Throws exception"

# Full triage with duplicate checking (seed registry from file)
jobstar-triage triage --title "Fix crash in API endpoint" --description "Throws exception" --registry-file goals.json

# Start the HTTP API server
jobstar-triage serve --port 8100
