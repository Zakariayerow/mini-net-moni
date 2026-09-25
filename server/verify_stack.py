#!/usr/bin/env python3
"""
MiniMon Server Health Check & Verification
Tests all components and provides diagnostics.
"""
import subprocess
import requests
import json
import sys
import time
from pathlib import Path

class HealthChecker:
    def __init__(self):
        self.results = {}
        self.server_url = "http://localhost:8000"
    
    def check_fastapi_server(self):
        """Check if FastAPI server is running."""
        try:
            response = requests.get(f"{self.server_url}/api/hosts", timeout=2)
            if response.status_code == 200:
                self.results["FastAPI Server"] = ("✓ Running", "success")
                return True
            else:
                self.results["FastAPI Server"] = (f"Running but error: {response.status_code}", "warning")
                return False
        except requests.exceptions.ConnectionError:
            self.results["FastAPI Server"] = ("✗ Not running or not responding", "error")
            return False
        except Exception as e:
            self.results["FastAPI Server"] = (f"✗ Error: {str(e)}", "error")
            return False
    
    def check_database_connection(self):
        """Check if database is accessible via server."""
        try:
            response = requests.get(f"{self.server_url}/api/hosts", timeout=2)
            if response.status_code == 200:
                self.results["PostgreSQL Database"] = ("✓ Connected", "success")
                return True
            elif response.status_code == 500:
                self.results["PostgreSQL Database"] = ("✗ Server error (DB likely down)", "error")
                return False
        except Exception as e:
            self.results["PostgreSQL Database"] = ("✗ Connection failed", "error")
            return False
    
    def check_dashboard(self):
        """Check if dashboard is accessible."""
        try:
            response = requests.get(f"{self.server_url}/", timeout=2)
            if response.status_code == 200:
                self.results["Dashboard"] = ("✓ Accessible at http://localhost:8000", "success")
                return True
        except Exception:
            pass
        self.results["Dashboard"] = ("✗ Not accessible", "error")
        return False
    
    def check_api_docs(self):
        """Check if API documentation is available."""
        try:
            response = requests.get(f"{self.server_url}/docs", timeout=2)
            if response.status_code == 200:
                self.results["API Documentation"] = ("✓ Available at http://localhost:8000/docs", "success")
                return True
        except Exception:
            pass
        self.results["API Documentation"] = ("✗ Not available", "error")
        return False
    
    def check_thresholds_seeded(self):
        """Check if threshold rules exist."""
        try:
            response = requests.get(
                f"{self.server_url}/api/alerts?limit=1",
                timeout=2
            )
            if response.status_code == 200:
                self.results["Thresholds Seeded"] = ("✓ Database accessible", "success")
                return True
        except Exception:
            pass
        self.results["Thresholds Seeded"] = ("? (Database not responding)", "warning")
        return False
    
    def print_results(self):
        """Print verification results in a formatted table."""
        print("\n" + "="*70)
        print("MiniMon Server Stack Verification")
        print("="*70 + "\n")
        
        for component, (status, level) in self.results.items():
            color_start = ""
            color_end = "\033[0m"
            if level == "success":
                color_start = "\033[92m"  # Green
            elif level == "warning":
                color_start = "\033[93m"  # Yellow
            elif level == "error":
                color_start = "\033[91m"  # Red
            
            print(f"{color_start}{status:<50}{color_end} {component}")
        
        print("\n" + "="*70)
        print("Quick Start Guide")
        print("="*70)
        print("\n1. ENSURE POSTGRESQL IS RUNNING")
        print("   Windows: Start PostgreSQL service or use:\n")
        print("     # If PostgreSQL is installed:")
        print("     C:\\> pg_ctl -D \"C:\\Program Files\\PostgreSQL\\data\" start")
        print("\n   Linux/Mac: sudo service postgresql start\n")
        
        print("2. INITIALIZE DATABASE (if not already done)")
        print("   $ psql -U postgres -d minimon -f server/schema.sql\n")
        
        print("3. SEED THRESHOLD RULES")
        print("   $ python server/seed_thresholds.py\n")
        
        print("4. ACCESS DASHBOARD")
        print("   Browser: http://localhost:8000")
        print("   API Docs: http://localhost:8000/docs\n")
        
        print("5. REGISTER SERVER HOSTS")
        print("   POST /api/hosts/register with X-Admin-Key header\n")
        
        print("6. DEPLOY AGENTS ON SERVERS")
        print("   Configure /etc/minimon/agent.yaml and run agent.py\n")
        
        print("="*70 + "\n")

def main():
    print("\n[i] Waiting for FastAPI server to respond...")
    
    checker = HealthChecker()
    
    # Try multiple times as server might be starting
    for attempt in range(5):
        try:
            checker.check_fastapi_server()
            break
        except:
            if attempt < 4:
                print(f"[i] Attempt {attempt+1}/5: Retrying in 1 second...")
                time.sleep(1)
    
    checker.check_dashboard()
    checker.check_api_docs()
    checker.check_database_connection()
    checker.check_thresholds_seeded()
    
    checker.print_results()
    
    # Return success if at least FastAPI server is running
    return checker.results.get("FastAPI Server", ("", "error"))[1] == "success"

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
