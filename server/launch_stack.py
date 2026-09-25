#!/usr/bin/env python3
"""
MiniMon Full Stack Launcher
Starts all server components: FastAPI server, SNMP poller, and database initialization.
Run: python launch_stack.py
"""
import os
import sys
import subprocess
import time
import threading
from pathlib import Path

# ANSI color codes for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_status(msg, status="info"):
    """Print colored status messages."""
    if status == "success":
        print(f"{Colors.GREEN}[✓]{Colors.RESET} {msg}")
    elif status == "error":
        print(f"{Colors.RED}[✗]{Colors.RESET} {msg}")
    elif status == "warning":
        print(f"{Colors.YELLOW}[!]{Colors.RESET} {msg}")
    elif status == "info":
        print(f"{Colors.CYAN}[i]{Colors.RESET} {msg}")
    elif status == "section":
        print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}{msg}{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.RESET}\n")

def check_postgres():
    """Check if PostgreSQL is available."""
    try:
        result = subprocess.run(
            ["psql", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print_status(f"PostgreSQL found: {result.stdout.strip()}", "success")
            return True
    except Exception as e:
        print_status(f"PostgreSQL not found in PATH: {e}", "error")
    return False

def seed_thresholds():
    """Seed initial threshold rules."""
    print_status("Seeding database thresholds...", "info")
    try:
        result = subprocess.run(
            ["python", "seed_thresholds.py"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print_status("Database thresholds seeded", "success")
            print(result.stdout)
            return True
        else:
            print_status("Failed to seed thresholds", "warning")
            print(result.stderr)
            return False
    except Exception as e:
        print_status(f"Error seeding thresholds: {e}", "error")
        return False

def start_fastapi_server():
    """Start the FastAPI server."""
    print_status("Starting FastAPI server...", "info")
    env = os.environ.copy()
    env["MINIMON_DB_DSN"] = env.get("MINIMON_DB_DSN", "dbname=minimon user=minimon password=changeme host=localhost")
    env["MINIMON_ADMIN_KEY"] = env.get("MINIMON_ADMIN_KEY", "changeme-admin-key")
    
    try:
        process = subprocess.Popen(
            ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=Path(__file__).parent,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        print_status("FastAPI server started (listening on http://localhost:8000)", "success")
        return process
    except Exception as e:
        print_status(f"Failed to start FastAPI server: {e}", "error")
        return None

def start_snmp_poller():
    """Start the SNMP poller."""
    print_status("Starting SNMP poller...", "info")
    env = os.environ.copy()
    env["MINIMON_DB_DSN"] = env.get("MINIMON_DB_DSN", "dbname=minimon user=minimon password=changeme host=localhost")
    env["MINIMON_SNMP_POLL_SECONDS"] = env.get("MINIMON_SNMP_POLL_SECONDS", "60")
    
    try:
        process = subprocess.Popen(
            ["python", "snmp_poller.py"],
            cwd=Path(__file__).parent,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        print_status("SNMP poller started", "success")
        return process
    except Exception as e:
        print_status(f"Failed to start SNMP poller: {e}", "error")
        return None

def monitor_process(process, name):
    """Monitor a process and print output."""
    if not process:
        return
    for line in iter(process.stdout.readline, ''):
        if line:
            print(f"[{name}] {line.rstrip()}")

def main():
    print_status("MiniMon Full Stack Launcher", "section")
    
    # Check PostgreSQL
    if not check_postgres():
        print_status("PostgreSQL is required! Please ensure it's installed and in PATH.", "error")
        print_status("Installation instructions:", "info")
        print("  Windows: Download from https://www.postgresql.org/download/windows/")
        print("  Linux: sudo apt install postgresql postgresql-contrib")
        print("  macOS: brew install postgresql")
        return False
    
    # Seed thresholds
    print_status("Database Setup", "section")
    seed_thresholds()
    
    # Start services
    print_status("Starting Services", "section")
    
    fastapi_proc = start_fastapi_server()
    time.sleep(2)  # Give server time to start
    
    snmp_proc = start_snmp_poller()
    
    # Print instructions
    print_status("Startup Complete", "section")
    print_status("Dashboard: http://localhost:8000", "success")
    print_status("API Docs: http://localhost:8000/docs", "success")
    print()
    print(f"{Colors.BOLD}Environment Variables:{Colors.RESET}")
    print(f"  MINIMON_DB_DSN: {os.environ.get('MINIMON_DB_DSN', 'dbname=minimon user=minimon password=changeme host=localhost')}")
    print(f"  MINIMON_ADMIN_KEY: {os.environ.get('MINIMON_ADMIN_KEY', 'changeme-admin-key')}")
    print(f"  MINIMON_SMTP_HOST: {os.environ.get('MINIMON_SMTP_HOST', '(not configured)')}")
    print()
    print(f"{Colors.BOLD}Server Components Running:{Colors.RESET}")
    print(f"  ✓ FastAPI Server (PID: {fastapi_proc.pid if fastapi_proc else 'N/A'})")
    print(f"  ✓ SNMP Poller (PID: {snmp_proc.pid if snmp_proc else 'N/A'})")
    print()
    
    # Monitor processes
    print_status("Press Ctrl+C to stop all services", "info")
    print()
    
    try:
        # Keep main thread alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print_status("\nShutting down services...", "info")
        if fastapi_proc:
            fastapi_proc.terminate()
        if snmp_proc:
            snmp_proc.terminate()
        time.sleep(1)
        if fastapi_proc and fastapi_proc.poll() is None:
            fastapi_proc.kill()
        if snmp_proc and snmp_proc.poll() is None:
            snmp_proc.kill()
        print_status("Services stopped", "success")
        return True
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
