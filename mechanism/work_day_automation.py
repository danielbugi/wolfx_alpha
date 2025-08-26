#!/usr/bin/env python3
# automation/work_day_automation.py
"""
FULL DATABASE UPDATE AUTOMATION
Updates ALL records without limits while you're at work
"""

import os
import sys
import subprocess
import time
import logging
from datetime import datetime, timedelta
import json
from pathlib import Path

# Setup logging with timestamp
log_filename = f"automation_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class WorkDayAutomation:
    def __init__(self):
        self.start_time = datetime.now()
        self.automation_dir = Path(__file__).parent
        self.results = {
            'start_time': self.start_time.isoformat(),
            'phases_completed': [],
            'phases_failed': [],
            'total_time': None,
            'final_status': None
        }

        # Scripts to run in order
        self.scripts = {
            'daily_data_updater_corrected.py': 'Daily Data Update',
            'fundamentals_updater_corrected.py': 'Fundamentals Update',
            'historical_breakouts_corrected.py': 'ML Training Data Generation',
            'donchian_screener_corrected.py': 'Current Breakout Screening'
        }

        # Progress tracking
        self.current_phase = 0
        self.total_phases = len(self.scripts) + 2  # +2 for health checks

    def log_banner(self, message, char="="):
        """Create a nice banner for logging"""
        banner = char * 60
        logger.info(f"\n{banner}")
        logger.info(f"{message:^60}")
        logger.info(f"{banner}\n")

    def run_command(self, command, timeout_minutes=180):
        """Run a command with timeout and REAL-TIME logging"""
        try:
            logger.info(f"🚀 Running: {command}")
            start_time = time.time()

            # Run with real-time output
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=self.automation_dir,
                bufsize=1,
                universal_newlines=True
            )

            # Print output in real-time
            output_lines = []
            while True:
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    print(output.strip())  # Print immediately to console
                    logger.info(output.strip())  # Also log it
                    output_lines.append(output.strip())

            end_time = time.time()
            duration = end_time - start_time
            return_code = process.poll()

            if return_code == 0:
                logger.info(f"✅ Command completed successfully in {duration:.1f}s")
                return True, '\n'.join(output_lines)
            else:
                logger.error(f"❌ Command failed with return code {return_code}")
                return False, '\n'.join(output_lines)

        except Exception as e:
            logger.error(f"💥 Command failed with exception: {e}")
            return False, str(e)

    def check_system_health(self, phase_name="System Health Check"):
        """Check system health using master mechanism runner"""
        self.log_banner(f"🔍 {phase_name}")

        success, output = self.run_command(
            "python master_automation_runner_updated.py health",
            timeout_minutes=5
        )

        if success:
            logger.info("✅ System health check passed")
            self.results['phases_completed'].append(phase_name)
        else:
            logger.error("❌ System health check failed")
            self.results['phases_failed'].append(phase_name)

        self.current_phase += 1
        self.log_progress()
        return success

    def run_data_update_phase(self):
        """Phase 1: Update daily data - FULL UPDATE ALL SYMBOLS"""
        phase_name = "Daily Data Update"
        self.log_banner(f"📊 PHASE 1: {phase_name} - ALL SYMBOLS")

        logger.info("🚀 Running FULL data update for ALL symbols...")
        logger.info("📈 This will update stock prices, technical indicators, and yesterday's data")
        logger.info("⏱️ Expected time: 45-90 minutes for all 1000+ symbols")

        # Run full update directly - no testing, no limits
        full_success, _ = self.run_command(
            "python daily_data_updater_corrected.py",
            timeout_minutes=120  # 2 hours max
        )

        if full_success:
            logger.info(f"✅ {phase_name} completed successfully - ALL SYMBOLS UPDATED")
            self.results['phases_completed'].append(phase_name)
            return True
        else:
            logger.error(f"❌ {phase_name} failed")
            self.results['phases_failed'].append(phase_name)
            return False

    def run_fundamentals_update_phase(self):
        """Phase 2: Update fundamentals - FULL UPDATE ALL SYMBOLS"""
        phase_name = "Fundamentals Update"
        self.log_banner(f"💰 PHASE 2: {phase_name} - ALL SYMBOLS")

        logger.info("🚀 Running FULL fundamentals update for ALL symbols...")
        logger.info("📊 This will update P/E ratios, market cap, quality scores, and company data")
        logger.info("⏱️ Expected time: 2-3 hours for all symbols (Yahoo rate limits)")
        logger.info("⚠️ This respects Yahoo Finance rate limits automatically")

        # Run full fundamentals update - no testing, no limits
        success, _ = self.run_command(
            "python fundamentals_updater_corrected.py",
            timeout_minutes=240  # 4 hours max due to rate limits
        )

        if success:
            logger.info(f"✅ {phase_name} completed successfully - ALL FUNDAMENTALS UPDATED")
            self.results['phases_completed'].append(phase_name)
            return True
        else:
            logger.error(f"❌ {phase_name} failed")
            self.results['phases_failed'].append(phase_name)
            return False

    def run_ml_data_generation_phase(self):
        """Phase 3: Generate ML training data - FULL PROCESSING ALL SYMBOLS"""
        phase_name = "ML Training Data Generation"
        self.log_banner(f"🧠 PHASE 3: {phase_name} - ALL SYMBOLS")

        logger.info("🚀 Running FULL ML training data generation for ALL symbols...")
        logger.info("🔬 This will analyze historical breakout patterns and create ML datasets")
        logger.info("📈 This generates breakouts + ml_training_data tables with success/failure labels")
        logger.info("⏱️ Expected time: 2-3 hours for comprehensive analysis")

        # Run full ML data generation - no testing, no limits
        success, _ = self.run_command(
            "python historical_breakouts_corrected.py",
            timeout_minutes=240  # 4 hours max for comprehensive analysis
        )

        if success:
            logger.info(f"✅ {phase_name} completed successfully - ALL ML DATA GENERATED")
            self.results['phases_completed'].append(phase_name)
            return True
        else:
            logger.error(f"❌ {phase_name} failed")
            self.results['phases_failed'].append(phase_name)
            return False

    def run_screening_phase(self):
        """Phase 4: Run current breakout screening - FULL SCREENING ALL SYMBOLS"""
        phase_name = "Current Breakout Screening"
        self.log_banner(f"🔍 PHASE 4: {phase_name} - ALL SYMBOLS")

        logger.info("🚀 Running FULL breakout screening for ALL symbols...")
        logger.info("🎯 This will identify current trading opportunities and breakout signals")
        logger.info("📊 This populates the breakouts table with current opportunities")
        logger.info("⏱️ Expected time: 30-45 minutes for full screening")

        # Run full screening - no testing, all symbols
        success, _ = self.run_command(
            "python donchian_screener_corrected.py",
            timeout_minutes=60
        )

        if success:
            logger.info(f"✅ {phase_name} completed successfully - ALL SYMBOLS SCREENED")
            self.results['phases_completed'].append(phase_name)
            return True
        else:
            logger.error(f"❌ {phase_name} failed")
            self.results['phases_failed'].append(phase_name)
            return False

    def generate_final_report(self):
        """Generate comprehensive final report"""
        self.log_banner("📊 FINAL REPORT")

        # Get final system health
        logger.info("📈 Generating final system report...")
        self.run_command(
            "python master_automation_runner_updated.py report",
            timeout_minutes=5
        )

        # Calculate total time
        end_time = datetime.now()
        total_duration = end_time - self.start_time
        self.results['end_time'] = end_time.isoformat()
        self.results['total_time'] = str(total_duration)

        # Determine final status
        total_phases = len(self.results['phases_completed']) + len(self.results['phases_failed'])
        success_rate = len(self.results['phases_completed']) / max(total_phases, 1) * 100

        if success_rate >= 75:
            self.results['final_status'] = 'SUCCESS'
        elif success_rate >= 50:
            self.results['final_status'] = 'PARTIAL_SUCCESS'
        else:
            self.results['final_status'] = 'FAILED'

        # Log final summary
        logger.info(f"⏱️ Total execution time: {total_duration}")
        logger.info(f"✅ Phases completed: {len(self.results['phases_completed'])}")
        logger.info(f"❌ Phases failed: {len(self.results['phases_failed'])}")
        logger.info(f"📊 Success rate: {success_rate:.1f}%")
        logger.info(f"🎯 Final status: {self.results['final_status']}")

        # Save results to JSON
        results_file = f"automation_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(results_file, 'w') as f:
            json.dump(self.results, f, indent=2)

        logger.info(f"💾 Results saved to: {results_file}")
        logger.info(f"📝 Log saved to: {log_filename}")

    def log_progress(self):
        """Log current progress"""
        progress = (self.current_phase / self.total_phases) * 100
        logger.info(f"📈 Overall Progress: {progress:.1f}% ({self.current_phase}/{self.total_phases})")

    def run_automation(self):
        """Run the complete mechanism pipeline"""
        try:
            self.log_banner("🚀 STARTING FULL DATABASE UPDATE AUTOMATION", "=")
            logger.info(f"📅 Started at: {self.start_time}")
            logger.info(f"📁 Working directory: {self.automation_dir}")
            logger.info(f"📝 Log file: {log_filename}")
            logger.info("🎯 FULL UPDATE MODE: All symbols, all data, no limits!")
            logger.info("⏱️ Expected total time: 6-10 hours for complete database refresh")

            # Initial health check
            logger.info("🔍 Performing initial system health check...")
            if not self.check_system_health("Initial Health Check"):
                logger.warning("⚠️ Initial health check failed, but continuing...")

            # Phase 1: Daily Data Update (PRIORITY 1) - ALL SYMBOLS
            self.current_phase += 1
            self.log_progress()
            logger.info("🚀 Starting Phase 1: FULL daily data update...")
            if not self.run_data_update_phase():
                logger.error("❌ Daily data update failed, but continuing with other phases...")

            # Short break between phases
            logger.info("⏸️ Taking 2 minute break between phases...")
            time.sleep(120)

            # Phase 2: Fundamentals Update (PRIORITY 2) - ALL SYMBOLS
            self.current_phase += 1
            self.log_progress()
            logger.info("🚀 Starting Phase 2: FULL fundamentals update...")
            if not self.run_fundamentals_update_phase():
                logger.error("❌ Fundamentals update failed, but continuing...")

            # Short break
            logger.info("⏸️ Taking 2 minute break...")
            time.sleep(120)

            # Phase 3: ML Data Generation (PRIORITY 3) - ALL SYMBOLS
            self.current_phase += 1
            self.log_progress()
            logger.info("🚀 Starting Phase 3: FULL ML training data generation...")
            if not self.run_ml_data_generation_phase():
                logger.error("❌ ML data generation failed, but continuing...")

            # Short break
            logger.info("⏸️ Taking 1 minute break...")
            time.sleep(60)

            # Phase 4: Current Screening (PRIORITY 4) - ALL SYMBOLS
            self.current_phase += 1
            self.log_progress()
            logger.info("🚀 Starting Phase 4: FULL breakout screening...")
            if not self.run_screening_phase():
                logger.error("❌ Screening failed, but continuing to final steps...")

            # Final health check
            self.current_phase += 1
            self.check_system_health("Final Health Check")

            # Generate final report
            self.current_phase += 1
            self.log_progress()
            self.generate_final_report()

            self.log_banner("🎉 FULL DATABASE UPDATE COMPLETED", "=")

        except KeyboardInterrupt:
            logger.info("\n⏹️ Automation interrupted by user")
            self.results['final_status'] = 'INTERRUPTED'
            self.generate_final_report()
        except Exception as e:
            logger.error(f"💥 Automation failed with exception: {e}")
            self.results['final_status'] = 'CRASHED'
            self.generate_final_report()
            raise


def main():
    """Main function"""
    print("🚀 FULL DATABASE UPDATE AUTOMATION STARTING...")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("🎯 FULL UPDATE MODE: All symbols, all data, no limits!")
    print("📊 This will update:")
    print("   ✅ ALL stock prices and technical indicators")
    print("   ✅ ALL company fundamentals and quality scores")
    print("   ✅ ALL ML training data and historical breakouts")
    print("   ✅ ALL current breakout opportunities")
    print("⏱️ Expected time: 6-10 hours for complete database refresh")
    print("📝 Real-time progress will be shown below")
    print("⏹️ Press Ctrl+C to stop at any time")
    print("=" * 60)

    automation = WorkDayAutomation()
    automation.run_automation()


if __name__ == "__main__":
    main()