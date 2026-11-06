# LinkedIn Job Discovery Agent

A Python-based automation project designed to support job discovery and tracking workflows.

The project provides a configurable application structure, local data storage, and automated processing of job-related information.

## Overview

The goal of this project is to reduce repetitive tasks involved in monitoring job opportunities and organizing relevant information.

## Features

- Python-based automation
- Configurable application settings
- Job-related data processing
- Local SQLite database
- JSON-based data storage
- Windows execution script

## Technology Stack

| Technology | Purpose |
|---|---|
| Python | Application logic |
| SQLite | Local data storage |
| JSON | Structured data exchange |
| Python dependencies | Application functionality |
| Windows Batch | Execution automation |

## Project Structure

- `linkedin_agent.py` — Main application
- `config.py` — Application configuration
- `requirements.txt` — Python dependencies
- `jobs.db` — Local database
- `new_jobs_latest.json` — Job-related output data
- `run.bat` — Windows execution script

## Getting Started

### Prerequisites

- Python 3
- Windows or a compatible Python environment

### Installation

Clone the repository:

```bash
git clone https://github.com/pgomezm/linkedin_agent.git
cd linkedin_agent
```

Install dependencies:

```bash
pip install -r requirements.txt
```

### Running

On Windows:

```bat
run.bat
```

Alternatively, run the Python application directly:

```bash
python linkedin_agent.py
```

Refer to the application configuration for additional setup requirements.

## Engineering Focus

- Python automation
- Data processing
- Local persistence
- Configurable application workflows
- Automation tooling

## Security and Privacy

Do not commit authentication credentials, session cookies, personal information, or private job-search data.

Use local configuration files and environment variables for sensitive settings.

## License

See the repository for licensing information.
