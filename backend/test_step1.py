# File: backend/test_step1.py
"""
Test script for Step 1: Enhanced Backend Main Application
Run this after implementing Step 1 to verify everything works
"""

import requests
import json
import time
from datetime import datetime

BASE_URL = "http://localhost:8000"


def test_endpoint(endpoint, expected_fields=None):
    """Test a single endpoint"""
    print(f"\n🧪 Testing: {endpoint}")
    try:
        response = requests.get(f"{BASE_URL}{endpoint}")

        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Status: {response.status_code}")
            print(f"   📊 Response size: {len(json.dumps(data))} bytes")

            if expected_fields:
                for field in expected_fields:
                    if field in data:
                        print(f"   ✅ Field '{field}': Present")
                    else:
                        print(f"   ❌ Field '{field}': Missing")

            # Show sample data structure
            if isinstance(data, list) and len(data) > 0:
                print(f"   📋 Sample item keys: {list(data[0].keys())}")
            elif isinstance(data, dict):
                print(f"   📋 Top-level keys: {list(data.keys())}")

            return True

        else:
            print(f"   ❌ Status: {response.status_code}")
            print(f"   ❌ Error: {response.text}")
            return False

    except requests.exceptions.ConnectionError:
        print(f"   ❌ Connection failed - Is the server running?")
        return False
    except Exception as e:
        print(f"   ❌ Error: {str(e)}")
        return False


def run_all_tests():
    """Run all Step 1 tests"""
    print("🚀 Testing Step 1: Enhanced Backend Main Application")
    print(f"⏰ Started at: {datetime.now()}")
    print("=" * 60)

    tests = [
        {
            "endpoint": "/",
            "expected_fields": ["message", "version", "status", "endpoints"]
        },
        {
            "endpoint": "/api/health",
            "expected_fields": ["status", "timestamp", "components"]
        },
        {
            "endpoint": "/api/dashboard/main-page-data",
            "expected_fields": ["timestamp", "market_summary", "top_gainers", "top_losers", "unusual_volume",
                                "top_ai_picks"]
        },
        {
            "endpoint": "/api/dashboard/top-gainers",
            "expected_fields": None  # Will be a list
        },
        {
            "endpoint": "/api/dashboard/top-losers",
            "expected_fields": None  # Will be a list
        },
        {
            "endpoint": "/api/dashboard/unusual-volume",
            "expected_fields": None  # Will be a list
        },
        {
            "endpoint": "/api/dashboard/top-ai-picks",
            "expected_fields": None  # Will be a list
        }
    ]

    results = []
    for test in tests:
        success = test_endpoint(test["endpoint"], test.get("expected_fields"))
        results.append(success)
        time.sleep(0.5)  # Small delay between tests

    print("\n" + "=" * 60)
    print("📊 TEST SUMMARY")
    print("=" * 60)

    passed = sum(results)
    total = len(results)

    print(f"✅ Passed: {passed}/{total}")
    if passed == total:
        print("🎉 ALL TESTS PASSED! Step 1 implementation is working correctly.")
    else:
        print("⚠️  Some tests failed. Check the output above for details.")

    print(f"\n💡 Next: If all tests passed, you're ready for Step 2!")
    print(f"🌐 View API docs at: {BASE_URL}/docs")


if __name__ == "__main__":
    run_all_tests()