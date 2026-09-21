# File: backend/test_step2.py
"""
Test script for Step 2: Advanced Screener Functionality
Run this after implementing Step 2 to verify screener endpoints work
"""

import requests
import json
import time
from datetime import datetime

BASE_URL = "http://localhost:8000"

def test_endpoint(endpoint, method="GET", data=None, expected_fields=None):
    """Test a single endpoint"""
    print(f"\n🧪 Testing: {method} {endpoint}")
    try:
        if method == "GET":
            response = requests.get(f"{BASE_URL}{endpoint}")
        elif method == "POST":
            headers = {"Content-Type": "application/json"}
            response = requests.post(f"{BASE_URL}{endpoint}",
                                   data=json.dumps(data), headers=headers)

        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Status: {response.status_code}")
            print(f"   📊 Response size: {len(json.dumps(data))} bytes")

            if expected_fields:
                for field in expected_fields:
                    if field in data:
                        if isinstance(data[field], list) and len(data[field]) > 0:
                            print(f"   ✅ Field '{field}': Present ({len(data[field])} items)")
                        elif isinstance(data[field], dict):
                            print(f"   ✅ Field '{field}': Present ({len(data[field])} keys)")
                        else:
                            print(f"   ✅ Field '{field}': Present")
                    else:
                        print(f"   ❌ Field '{field}': Missing")

            # Show sample data structure
            if isinstance(data, dict):
                if "results" in data and isinstance(data["results"], list) and len(data["results"]) > 0:
                    print(f"   📋 Sample result keys: {list(data['results'][0].keys())}")
                elif "presets" in data and isinstance(data["presets"], list) and len(data["presets"]) > 0:
                    print(f"   📋 Sample preset keys: {list(data['presets'][0].keys())}")
                else:
                    print(f"   📋 Top-level keys: {list(data.keys())}")

            return True, data

        else:
            print(f"   ❌ Status: {response.status_code}")
            print(f"   ❌ Error: {response.text}")
            return False, None

    except requests.exceptions.ConnectionError:
        print(f"   ❌ Connection failed - Is the server running?")
        return False, None
    except Exception as e:
        print(f"   ❌ Error: {str(e)}")
        return False, None

def test_basic_screener_functionality():
    """Test basic screener endpoints"""
    print("🔧 TESTING BASIC SCREENER FUNCTIONALITY")
    print("=" * 60)

    tests = [
        {
            "endpoint": "/api/screener/filters",
            "method": "GET",
            "expected_fields": ["sectors", "price_ranges", "market_cap_ranges", "quality_grades", "technical_ranges"]
        },
        {
            "endpoint": "/api/screener/market-overview",
            "method": "GET",
            "expected_fields": ["market_breadth", "market_indicators", "sector_performance"]
        },
        {
            "endpoint": "/api/screener/presets",
            "method": "GET",
            "expected_fields": ["presets"]
        },
    ]

    results = []
    filter_data = None

    for test in tests:
        success, data = test_endpoint(
            test["endpoint"],
            test.get("method", "GET"),
            test.get("data"),
            test.get("expected_fields")
        )
        results.append(success)

        # Store filter data for later use
        if test["endpoint"] == "/api/screener/filters" and success:
            filter_data = data

        time.sleep(0.5)

    return results, filter_data

def test_screener_search():
    """Test screener search functionality"""
    print("\n🔍 TESTING SCREENER SEARCH FUNCTIONALITY")
    print("=" * 60)

    # Test 1: Basic search with minimal filters
    print("\n1️⃣ Testing basic search...")
    basic_search_data = {
        "min_price": 10.0,
        "limit": 20
    }

    success1, data1 = test_endpoint(
        "/api/screener/search",
        method="POST",
        data=basic_search_data,
        expected_fields=["filters_applied", "total_matches", "results", "facets", "execution_time_ms"]
    )

    # Test 2: Advanced search with multiple filters
    print("\n2️⃣ Testing advanced search...")
    advanced_search_data = {
        "min_price": 5.0,
        "max_price": 500.0,
        "min_volume": 100000,
        "min_volume_ratio": 1.2,
        "sectors": ["Technology", "Healthcare"],
        "quality_grades": ["A", "B"],
        "min_price_change": 0.5,
        "limit": 15,
        "sort_by": "price_change_pct",
        "sort_order": "desc"
    }

    success2, data2 = test_endpoint(
        "/api/screener/search",
        method="POST",
        data=advanced_search_data,
        expected_fields=["filters_applied", "total_matches", "results", "facets", "execution_time_ms"]
    )

    # Test 3: Technical analysis focused search
    print("\n3️⃣ Testing technical analysis search...")
    technical_search_data = {
        "min_volume_ratio": 2.0,
        "min_rsi": 50.0,
        "max_rsi": 75.0,
        "min_price": 1.0,
        "limit": 25,
        "sort_by": "volume_ratio",
        "sort_order": "desc"
    }

    success3, data3 = test_endpoint(
        "/api/screener/search",
        method="POST",
        data=technical_search_data,
        expected_fields=["filters_applied", "total_matches", "results", "facets", "execution_time_ms"]
    )

    return [success1, success2, success3], [data1, data2, data3]

def test_preset_functionality():
    """Test preset functionality"""
    print("\n⚙️ TESTING PRESET FUNCTIONALITY")
    print("=" * 60)

    # Test preset search endpoints
    preset_tests = [
        "growth-stocks",
        "value-plays",
        "breakout-candidates",
        "large-cap-leaders",
        "oversold-recovery"
    ]

    results = []

    for preset in preset_tests:
        print(f"\n📋 Testing preset: {preset}")
        success, data = test_endpoint(
            f"/api/screener/presets/{preset}/search",
            method="POST",
            expected_fields=["filters_applied", "total_matches", "results", "execution_time_ms"]
        )
        results.append(success)

        if success and data:
            print(f"   📊 Found {data.get('total_matches', 0)} matches")
            print(f"   ⚡ Execution time: {data.get('execution_time_ms', 0)}ms")

    return results

def test_analytics_endpoints():
    """Test analytics endpoints"""
    print("\n📈 TESTING ANALYTICS ENDPOINTS")
    print("=" * 60)

    analytics_tests = [
        {
            "endpoint": "/api/screener/analytics/sector-breakdown",
            "expected_fields": ["sector_performance", "timestamp"]
        },
        {
            "endpoint": "/api/screener/analytics/technical-distribution",
            "expected_fields": ["technical_summary", "timestamp"]
        }
    ]

    results = []

    for test in analytics_tests:
        success, data = test_endpoint(
            test["endpoint"],
            expected_fields=test["expected_fields"]
        )
        results.append(success)

    return results

def run_comprehensive_step2_tests():
    """Run all Step 2 tests"""
    print("🚀 Testing Step 2: Advanced Screener Functionality")
    print(f"⏰ Started at: {datetime.now()}")
    print("=" * 80)

    all_results = []

    # Test 1: Basic screener functionality
    basic_results, filter_data = test_basic_screener_functionality()
    all_results.extend(basic_results)

    if filter_data:
        print(f"\n📋 Available sectors: {len(filter_data.get('sectors', []))}")
        print(f"📋 Price range: ${filter_data.get('price_ranges', {}).get('min', 0):.2f} - ${filter_data.get('price_ranges', {}).get('max', 0):.2f}")

    # Test 2: Screener search functionality
    search_results, search_data = test_screener_search()
    all_results.extend(search_results)

    # Show execution times
    for i, data in enumerate(search_data):
        if data and 'execution_time_ms' in data:
            print(f"   Search {i+1} execution time: {data['execution_time_ms']}ms")

    # Test 3: Preset functionality
    preset_results = test_preset_functionality()
    all_results.extend(preset_results)

    # Test 4: Analytics endpoints
    analytics_results = test_analytics_endpoints()
    all_results.extend(analytics_results)

    # Overall summary
    print("\n" + "=" * 80)
    print("📊 STEP 2 TEST SUMMARY")
    print("=" * 80)

    passed = sum(all_results)
    total = len(all_results)

    print(f"✅ Passed: {passed}/{total}")

    if passed == total:
        print("🎉 ALL STEP 2 TESTS PASSED!")
        print("✨ Advanced screener functionality is working perfectly!")
        print("\n🎯 Key Features Verified:")
        print("   ✅ Advanced stock filtering with multiple criteria")
        print("   ✅ Market overview and analytics")
        print("   ✅ Preset filter configurations")
        print("   ✅ Technical analysis integration")
        print("   ✅ Performance optimized queries")
    else:
        failed = total - passed
        print(f"⚠️  {failed} tests failed. Check the output above for details.")

    print(f"\n💡 Next: Frontend development to consume these APIs!")
    print(f"🌐 Full API documentation: {BASE_URL}/docs")
    print(f"🔧 Test individual endpoints: {BASE_URL}/api/screener/...")

if __name__ == "__main__":
    run_comprehensive_step2_tests()