#!/usr/bin/env python3
"""
MiniMon API Test Suite
Tests core API endpoints to verify server functionality.
Run: python test_api.py
"""
import requests
import json
import sys
from datetime import datetime

class APITester:
    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = base_url
        self.admin_key = "changeme-admin-key"
        self.test_hostname = "test-server-01"
        self.test_api_key = None
        self.results = []
    
    def print_test(self, name, passed, message=""):
        status = "✓" if passed else "✗"
        color = "\033[92m" if passed else "\033[91m"
        reset = "\033[0m"
        self.results.append((name, passed))
        print(f"{color}[{status}]{reset} {name}")
        if message:
            print(f"    {message}")
    
    def test_server_health(self):
        """Test if server is responding."""
        try:
            response = requests.get(f"{self.base_url}/", timeout=5)
            passed = response.status_code == 200
            self.print_test(
                "Server Health Check",
                passed,
                f"HTTP {response.status_code}"
            )
            return passed
        except Exception as e:
            self.print_test("Server Health Check", False, str(e))
            return False
    
    def test_api_docs(self):
        """Test if API documentation is available."""
        try:
            response = requests.get(f"{self.base_url}/docs", timeout=5)
            passed = response.status_code == 200
            self.print_test(
                "API Documentation",
                passed,
                "Swagger UI available at /docs"
            )
            return passed
        except Exception as e:
            self.print_test("API Documentation", False, str(e))
            return False
    
    def test_register_host(self):
        """Test host registration."""
        try:
            response = requests.post(
                f"{self.base_url}/api/hosts/register",
                params={
                    "hostname": self.test_hostname,
                    "group_name": "test"
                },
                headers={"X-Admin-Key": self.admin_key},
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                self.test_api_key = data.get("api_key")
                self.print_test(
                    "Host Registration",
                    True,
                    f"Registered {self.test_hostname}"
                )
                return True
            else:
                self.print_test(
                    "Host Registration",
                    False,
                    f"HTTP {response.status_code}: {response.text}"
                )
                return False
        except Exception as e:
            self.print_test("Host Registration", False, str(e))
            return False
    
    def test_list_hosts(self):
        """Test listing registered hosts."""
        try:
            response = requests.get(
                f"{self.base_url}/api/hosts",
                timeout=5
            )
            if response.status_code == 200:
                hosts = response.json()
                self.print_test(
                    "List Hosts",
                    True,
                    f"Found {len(hosts)} host(s)"
                )
                return True
            else:
                self.print_test(
                    "List Hosts",
                    False,
                    f"HTTP {response.status_code}"
                )
                return False
        except Exception as e:
            self.print_test("List Hosts", False, str(e))
            return False
    
    def test_submit_metrics(self):
        """Test metric submission."""
        if not self.test_api_key:
            self.print_test("Submit Metrics", False, "No API key available")
            return False
        
        try:
            payload = {
                "cpu_percent": 42.5,
                "mem_percent": 60.2,
                "disk_percent": 75.8,
                "net_sent_kb": 1024.5,
                "net_recv_kb": 2048.3,
                "load1": 2.3
            }
            response = requests.post(
                f"{self.base_url}/api/metrics",
                json=payload,
                headers={"X-API-Key": self.test_api_key},
                timeout=5
            )
            if response.status_code == 200:
                self.print_test(
                    "Submit Metrics",
                    True,
                    f"Submitted metrics for {self.test_hostname}"
                )
                return True
            else:
                self.print_test(
                    "Submit Metrics",
                    False,
                    f"HTTP {response.status_code}: {response.text[:100]}"
                )
                return False
        except Exception as e:
            self.print_test("Submit Metrics", False, str(e))
            return False
    
    def test_get_host_metrics(self):
        """Test retrieving host metrics."""
        try:
            response = requests.get(
                f"{self.base_url}/api/metrics/{self.test_hostname}?limit=10",
                timeout=5
            )
            if response.status_code == 200:
                metrics = response.json()
                self.print_test(
                    "Get Host Metrics",
                    True,
                    f"Retrieved {len(metrics)} metric record(s)"
                )
                return True
            else:
                self.print_test(
                    "Get Host Metrics",
                    False,
                    f"HTTP {response.status_code}"
                )
                return False
        except Exception as e:
            self.print_test("Get Host Metrics", False, str(e))
            return False
    
    def test_list_alerts(self):
        """Test retrieving alerts."""
        try:
            response = requests.get(
                f"{self.base_url}/api/alerts?limit=10",
                timeout=5
            )
            if response.status_code == 200:
                alerts = response.json()
                self.print_test(
                    "List Alerts",
                    True,
                    f"Retrieved {len(alerts)} alert(s)"
                )
                return True
            else:
                self.print_test(
                    "List Alerts",
                    False,
                    f"HTTP {response.status_code}"
                )
                return False
        except Exception as e:
            self.print_test("List Alerts", False, str(e))
            return False
    
    def test_list_devices(self):
        """Test retrieving network devices."""
        try:
            response = requests.get(
                f"{self.base_url}/api/devices",
                timeout=5
            )
            if response.status_code == 200:
                devices = response.json()
                self.print_test(
                    "List Devices",
                    True,
                    f"Found {len(devices)} network device(s)"
                )
                return True
            else:
                self.print_test(
                    "List Devices",
                    False,
                    f"HTTP {response.status_code}"
                )
                return False
        except Exception as e:
            self.print_test("List Devices", False, str(e))
            return False
    
    def print_summary(self):
        """Print test summary."""
        passed = sum(1 for _, p in self.results if p)
        total = len(self.results)
        
        print(f"\n{'='*60}")
        print(f"Test Results: {passed}/{total} passed")
        print(f"{'='*60}\n")
        
        if passed == total:
            print("\033[92m✓ All tests passed! Server is fully functional.\033[0m\n")
            return True
        else:
            print(f"\033[93m{total - passed} test(s) failed. Check server status.\033[0m\n")
            # List failed tests
            failed = [name for name, p in self.results if not p]
            print("Failed tests:")
            for test in failed:
                print(f"  - {test}")
            print()
            return False
    
    def run_all(self):
        """Run all tests."""
        print(f"\n{'='*60}")
        print(f"MiniMon API Test Suite")
        print(f"Target: {self.base_url}")
        print(f"Time: {datetime.now().isoformat()}")
        print(f"{'='*60}\n")
        
        self.test_server_health()
        self.test_api_docs()
        self.test_register_host()
        self.test_list_hosts()
        self.test_submit_metrics()
        self.test_get_host_metrics()
        self.test_list_alerts()
        self.test_list_devices()
        
        return self.print_summary()

def main():
    tester = APITester()
    success = tester.run_all()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
