# automation/orchestrators/master_automation_runner.py
"""
Refactored master mechanism runner using shared infrastructure
Orchestrates all trading system components with enhanced monitoring and reporting
"""

import os
import sys
import subprocess
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import argparse
import json

# Import shared infrastructure
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from shared import (
    config, db, setup_logging, timing_decorator,
    performance_monitor, file_utils, format_number,
    date_utils, get_system_stats
)

# Setup logging for this module
logger = setup_logging("master_automation_runner")


class TradingSystemOrchestrator:
    """Enhanced trading system orchestrator with comprehensive monitoring"""

    def __init__(self):
        """Initialize the orchestrator with shared infrastructure"""
        self.start_time = datetime.now()
        self.reports_dir = config.reports_dir
        self.frontend_data_dir = config.frontend_data_dir

        # Ensure directories exist
        file_utils.ensure_directory(self.reports_dir)
        file_utils.ensure_directory(self.frontend_data_dir)

        logger.info("Trading System Orchestrator initialized")

    @timing_decorator()
    def run_comprehensive_health_check(self) -> Dict[str, Any]:
        """Run comprehensive health check on the trading system"""
        logger.info("🏥 Running comprehensive health check...")

        health_status = {
            'database_connection': False,
            'core_data_tables': {},
            'ml_ready_tables': {},
            'utility_tables': {},
            'data_quality': {},
            'recent_activity': {},
            'system_summary': {},
            'performance_metrics': {},
            'system_resources': {}
        }

        try:
            # Database connection test
            db_status = db.test_connection()
            health_status['database_connection'] = db_status['connected']

            if not db_status['connected']:
                health_status['error'] = db_status['error']
                return health_status

            logger.info("✅ Database connection: OK")

            # Core trading data tables
            core_tables = {
                'stock_prices': 'Core price data',
                'technical_indicators': 'Technical analysis data',
                'daily_fundamentals': 'Company fundamentals',
                'quarterly_fundamentals': 'Detailed quarterly financials'
            }

            for table, description in core_tables.items():
                try:
                    count_query = f"SELECT COUNT(*) FROM {table}"
                    count = db.execute_query(count_query)[0][0]

                    # Get date range for time-series tables
                    date_info = {}
                    if table in ['stock_prices', 'technical_indicators', 'daily_fundamentals']:
                        date_query = f"SELECT MIN(date), MAX(date) FROM {table}"
                        date_result = db.execute_query(date_query)
                        if date_result[0][0]:
                            date_info = {
                                'earliest_date': date_result[0][0].isoformat(),
                                'latest_date': date_result[0][1].isoformat(),
                                'date_span_days': (date_result[0][1] - date_result[0][0]).days
                            }

                    health_status['core_data_tables'][table] = {
                        'count': count,
                        'status': 'healthy' if count > 0 else 'empty',
                        'description': description,
                        **date_info
                    }

                    status_icon = "✅" if count > 0 else "⚠️"
                    logger.info(f"{status_icon} {description}: {format_number(count)} records")

                except Exception as e:
                    health_status['core_data_tables'][table] = {
                        'count': 0,
                        'status': 'error',
                        'error': str(e)
                    }
                    logger.error(f"❌ {description}: {e}")

            # ML and analysis tables
            ml_tables = {
                'breakouts': 'Breakout detection results',
                'ml_training_data': 'ML training dataset'
            }

            for table, description in ml_tables.items():
                try:
                    count_query = f"SELECT COUNT(*) FROM {table}"
                    count = db.execute_query(count_query)[0][0]

                    # Get success rate for breakouts
                    additional_info = {}
                    if table == 'breakouts' and count > 0:
                        success_query = """
                            SELECT 
                                COUNT(*) as total,
                                SUM(CASE WHEN success THEN 1 ELSE 0 END) as successful,
                                breakout_type
                            FROM breakouts 
                            WHERE success IS NOT NULL
                            GROUP BY breakout_type
                        """
                        success_results = db.execute_dict_query(success_query)
                        additional_info['success_rates'] = {
                            row['breakout_type']: {
                                'total': row['total'],
                                'successful': row['successful'],
                                'rate': round(row['successful'] / row['total'] * 100, 1) if row['total'] > 0 else 0
                            } for row in success_results
                        }

                    health_status['ml_ready_tables'][table] = {
                        'count': count,
                        'status': 'ready' if count > 0 else 'empty',
                        'description': description,
                        **additional_info
                    }

                    status_icon = "🧠" if count > 0 else "💤"
                    logger.info(f"{status_icon} {description}: {format_number(count)} records")

                except Exception as e:
                    health_status['ml_ready_tables'][table] = {
                        'count': 0,
                        'status': 'error',
                        'error': str(e)
                    }
                    logger.error(f"❌ {description}: {e}")

            # Data quality analysis
            logger.info("🔍 Analyzing data quality...")

            try:
                # Recent data availability
                recent_prices_query = """
                    SELECT COUNT(DISTINCT symbol) as symbols, COUNT(*) as records
                    FROM stock_prices 
                    WHERE date >= CURRENT_DATE - INTERVAL '7 days'
                """
                recent_prices = db.execute_dict_query(recent_prices_query)[0]

                # Unique symbols and date coverage
                coverage_query = """
                    SELECT 
                        COUNT(DISTINCT symbol) as unique_symbols,
                        MIN(date) as earliest_date,
                        MAX(date) as latest_date
                    FROM stock_prices
                """
                coverage = db.execute_dict_query(coverage_query)[0]

                # Quality score analysis
                quality_query = """
                    SELECT 
                        AVG(overall_quality_score) as avg_quality,
                        MIN(overall_quality_score) as min_quality,
                        MAX(overall_quality_score) as max_quality,
                        COUNT(*) as total_scored,
                        COUNT(DISTINCT symbol) as symbols_with_scores
                    FROM daily_fundamentals 
                    WHERE overall_quality_score IS NOT NULL
                """
                quality_stats = db.execute_dict_query(quality_query)[0]

                health_status['data_quality'] = {
                    'unique_symbols': coverage['unique_symbols'],
                    'date_range': {
                        'start': coverage['earliest_date'].isoformat() if coverage['earliest_date'] else None,
                        'end': coverage['latest_date'].isoformat() if coverage['latest_date'] else None,
                        'span_days': (coverage['latest_date'] - coverage['earliest_date']).days if coverage[
                                                                                                       'earliest_date'] and
                                                                                                   coverage[
                                                                                                       'latest_date'] else 0
                    },
                    'recent_activity': {
                        'symbols_updated_7d': recent_prices['symbols'],
                        'price_records_7d': recent_prices['records']
                    },
                    'quality_scores': {
                        'average': round(quality_stats['avg_quality'], 2) if quality_stats['avg_quality'] else 0,
                        'range': f"{quality_stats['min_quality']}-{quality_stats['max_quality']}" if quality_stats[
                            'min_quality'] else "N/A",
                        'total_scored': quality_stats['total_scored'],
                        'symbols_with_scores': quality_stats['symbols_with_scores']
                    }
                }

                logger.info(f"📊 Unique symbols: {format_number(coverage['unique_symbols'])}")
                logger.info(f"📅 Date range: {coverage['earliest_date']} to {coverage['latest_date']}")
                logger.info(f"📈 Recent activity: {format_number(recent_prices['records'])} records (7 days)")

            except Exception as e:
                logger.error(f"❌ Error analyzing data quality: {e}")
                health_status['data_quality'] = {'error': str(e)}

            # Recent breakout activity
            try:
                breakout_activity_query = """
                    SELECT 
                        breakout_type,
                        COUNT(*) as count,
                        AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) * 100 as success_rate,
                        AVG(max_gain_10d) as avg_max_gain
                    FROM breakouts 
                    WHERE date >= CURRENT_DATE - INTERVAL '30 days'
                    GROUP BY breakout_type
                """
                recent_breakouts = db.execute_dict_query(breakout_activity_query)

                health_status['recent_activity'] = {
                    'breakouts_30d': {
                        row['breakout_type']: {
                            'count': row['count'],
                            'success_rate': round(row['success_rate'], 1) if row['success_rate'] else 0,
                            'avg_max_gain': round(row['avg_max_gain'], 2) if row['avg_max_gain'] else 0
                        } for row in recent_breakouts
                    }
                }

                if recent_breakouts:
                    logger.info("🚀 Recent breakout activity (30 days):")
                    for row in recent_breakouts:
                        logger.info(
                            f"   {row['breakout_type']}: {row['count']} breakouts ({row['success_rate']:.1f}% success)")

            except Exception as e:
                logger.error(f"❌ Error analyzing recent activity: {e}")
                health_status['recent_activity'] = {'error': str(e)}

            # Performance metrics
            health_status['performance_metrics'] = performance_monitor.get_summary(hours=24)

            # System resources
            health_status['system_resources'] = get_system_stats()

            # System summary
            total_records = sum([
                health_status['core_data_tables'].get('stock_prices', {}).get('count', 0),
                health_status['core_data_tables'].get('technical_indicators', {}).get('count', 0),
                health_status['core_data_tables'].get('daily_fundamentals', {}).get('count', 0),
                health_status['ml_ready_tables'].get('breakouts', {}).get('count', 0)
            ])

            ml_ready = health_status['ml_ready_tables'].get('ml_training_data', {}).get('count', 0) > 0
            core_healthy = all(
                table_info.get('count', 0) > 0
                for table_info in health_status['core_data_tables'].values()
                if isinstance(table_info, dict)
            )

            health_status['system_summary'] = {
                'total_records': total_records,
                'database_name': config.db_name,
                'system_status': 'production_ready' if core_healthy and ml_ready else 'partial_ready',
                'ml_ready': ml_ready,
                'api_ready': total_records > 100000,
                'core_healthy': core_healthy,
                'uptime_seconds': (datetime.now() - self.start_time).total_seconds()
            }

            # Overall status determination
            if core_healthy and ml_ready and total_records > 100000:
                logger.info("🎉 SYSTEM STATUS: PRODUCTION READY!")
                logger.info("✅ Core data: Excellent")
                logger.info("🧠 ML capabilities: Ready")
                logger.info("🚀 API ready: Yes")
            elif core_healthy:
                logger.info("✅ SYSTEM STATUS: CORE READY!")
                logger.info("⚠️ ML training data needs enhancement")
            else:
                logger.error("❌ SYSTEM STATUS: NEEDS ATTENTION")

            return health_status

        except Exception as e:
            logger.error(f"❌ Health check failed: {e}")
            health_status['error'] = str(e)
            return health_status

    def get_top_breakouts(self, limit: int = 10, days: int = 30) -> List[Dict]:
        """Get top recent breakouts with enhanced data"""
        try:
            query = """
                SELECT 
                    b.symbol,
                    b.date,
                    b.breakout_type,
                    b.entry_price,
                    b.volume_ratio,
                    b.success,
                    b.max_gain_10d,
                    b.max_loss_10d,
                    df.overall_quality_score,
                    df.quality_grade,
                    df.sector,
                    df.market_cap,
                    sp.close as current_price
                FROM breakouts b
                LEFT JOIN daily_fundamentals df ON b.symbol = df.symbol 
                    AND df.date = (SELECT MAX(date) FROM daily_fundamentals WHERE symbol = b.symbol)
                LEFT JOIN stock_prices sp ON b.symbol = sp.symbol
                    AND sp.date = (SELECT MAX(date) FROM stock_prices WHERE symbol = b.symbol)
                WHERE b.date >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY b.max_gain_10d DESC NULLS LAST, b.volume_ratio DESC
                LIMIT %s
            """

            results = db.execute_dict_query(query, (days, limit))

            breakouts = []
            for row in results:
                # Calculate current performance if possible
                current_performance = None
                if row['current_price'] and row['entry_price']:
                    current_performance = ((row['current_price'] - row['entry_price']) / row['entry_price']) * 100

                breakout = {
                    'symbol': row['symbol'],
                    'date': row['date'].isoformat() if row['date'] else None,
                    'breakout_type': row['breakout_type'],
                    'entry_price': float(row['entry_price']) if row['entry_price'] else 0,
                    'current_price': float(row['current_price']) if row['current_price'] else None,
                    'current_performance_pct': round(current_performance, 2) if current_performance else None,
                    'volume_ratio': float(row['volume_ratio']) if row['volume_ratio'] else 0,
                    'success': row['success'],
                    'max_gain_10d': float(row['max_gain_10d']) if row['max_gain_10d'] else 0,
                    'max_loss_10d': float(row['max_loss_10d']) if row['max_loss_10d'] else 0,
                    'quality_score': float(row['overall_quality_score']) if row['overall_quality_score'] else 0,
                    'quality_grade': row['quality_grade'],
                    'sector': row['sector'],
                    'market_cap': row['market_cap'],
                    'market_cap_formatted': format_number(row['market_cap']) if row['market_cap'] else 'N/A'
                }
                breakouts.append(breakout)

            return breakouts

        except Exception as e:
            logger.error(f"Error getting top breakouts: {e}")
            return []

    @timing_decorator()
    def generate_comprehensive_report(self) -> str:
        """Generate comprehensive system report with enhanced metrics"""
        try:
            logger.info("📊 Generating comprehensive system report...")

            health = self.run_comprehensive_health_check()
            top_breakouts = self.get_top_breakouts(10, 30)

            # System status emoji
            status_emoji = "🎉" if health['system_summary'].get('system_status') == 'production_ready' else "⚠️"

            report = f"""
# {status_emoji} ADVANCED TRADING SYSTEM REPORT
## Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

### 🏆 SYSTEM STATUS: {health['system_summary'].get('system_status', 'unknown').upper().replace('_', ' ')}

### 📊 Core Data Infrastructure:
"""

            # Core data tables
            for table, info in health.get('core_data_tables', {}).items():
                if isinstance(info, dict):
                    status_icon = "✅" if info.get('count', 0) > 0 else "⚠️"
                    count_str = format_number(info.get('count', 0))
                    report += f"- {status_icon} **{info.get('description', table)}:** {count_str} records"

                    if 'date_span_days' in info:
                        report += f" ({info['date_span_days']} days coverage)"
                    report += "\n"

            # ML & Analysis tables
            report += f"\n### 🧠 ML & Analysis Infrastructure:\n"
            for table, info in health.get('ml_ready_tables', {}).items():
                if isinstance(info, dict):
                    status_icon = "🧠" if info.get('count', 0) > 0 else "💤"
                    count_str = format_number(info.get('count', 0))
                    report += f"- {status_icon} **{info.get('description', table)}:** {count_str} records"

                    if 'success_rates' in info:
                        rates = info['success_rates']
                        rate_summary = ", ".join([f"{k}: {v['rate']:.1f}%" for k, v in rates.items()])
                        report += f" (Success rates: {rate_summary})"
                    report += "\n"

            # Data quality section
            quality = health.get('data_quality', {})
            if quality and 'error' not in quality:
                report += f"""
### 📈 Data Quality Metrics:
- **Unique Symbols:** {format_number(quality.get('unique_symbols', 0))}
- **Date Coverage:** {quality.get('date_range', {}).get('start')} to {quality.get('date_range', {}).get('end')} ({quality.get('date_range', {}).get('span_days', 0)} days)
- **Recent Activity:** {format_number(quality.get('recent_activity', {}).get('price_records_7d', 0))} price updates (7 days)
- **Quality Scores:** Average {quality.get('quality_scores', {}).get('average', 0)}/100 ({format_number(quality.get('quality_scores', {}).get('symbols_with_scores', 0))} symbols scored)
"""

            # Recent performance
            recent_activity = health.get('recent_activity', {})
            if recent_activity and 'breakouts_30d' in recent_activity:
                report += f"\n### 🚀 Recent Breakout Performance (30 days):\n"
                for breakout_type, stats in recent_activity['breakouts_30d'].items():
                    report += f"- **{breakout_type.title()}:** {stats['count']} breakouts ({stats['success_rate']:.1f}% success, {stats['avg_max_gain']:.2f}% avg gain)\n"

            # System resources
            resources = health.get('system_resources', {})
            if resources and 'error' not in resources:
                report += f"""
### 💻 System Resources:
- **CPU Usage:** {resources.get('cpu_percent', 0):.1f}%
- **Memory:** {resources.get('memory', {}).get('used_percent', 0):.1f}% used ({resources.get('memory', {}).get('available_gb', 0):.1f}GB available)
- **Disk:** {resources.get('disk', {}).get('used_percent', 0):.1f}% used ({resources.get('disk', {}).get('free_gb', 0):.1f}GB free)
"""

            # Performance metrics
            perf_metrics = health.get('performance_metrics', {})
            if perf_metrics and 'monitoring' not in perf_metrics:
                report += f"""
### ⚡ Performance Metrics (24h):
- **Total Operations:** {perf_metrics.get('total_operations', 0)}
- **Success Rate:** {perf_metrics.get('success_rate_percent', 0)}%
- **Avg Execution Time:** {perf_metrics.get('execution_time', {}).get('avg_seconds', 0):.3f}s
- **Peak Memory Usage:** {perf_metrics.get('memory_usage_mb', {}).get('max', 0):.1f}MB
"""

            # Top breakouts
            if top_breakouts:
                report += f"\n### 🏆 Top Recent Breakouts (30 days):\n"
                for i, breakout in enumerate(top_breakouts[:5], 1):
                    success_icon = "✅" if breakout['success'] else "❌"
                    current_perf = f" (Current: {breakout['current_performance_pct']:+.1f}%)" if breakout[
                        'current_performance_pct'] else ""

                    report += f"""
{i}. **{breakout['symbol']}** ({breakout.get('sector', 'N/A')}) {success_icon}
   - Date: {breakout['date']} | Type: {breakout['breakout_type']}
   - Entry: ${breakout['entry_price']:.2f} | Max Gain: {breakout['max_gain_10d']:.1f}%{current_perf}
   - Quality: {breakout['quality_grade']} ({breakout['quality_score']:.0f}/100) | Market Cap: {breakout['market_cap_formatted']}
"""

            # System capabilities and recommendations
            summary = health.get('system_summary', {})
            report += f"""
### 🎯 System Capabilities:
- **✅ Database Infrastructure:** {format_number(summary.get('total_records', 0))} total records
- **✅ Real-time Data Pipeline:** {'Active' if summary.get('core_healthy') else 'Needs Attention'}
- **✅ ML Training Ready:** {'Yes' if summary.get('ml_ready') else 'Partial'}
- **✅ API Backend Ready:** {'Yes' if summary.get('api_ready') else 'No'}
- **✅ Frontend Data Generation:** {'Active' if os.path.exists(self.frontend_data_dir) else 'Not Configured'}
- **✅ Breakout Detection:** Advanced Donchian Channel Analysis
- **✅ Quality Scoring:** Comprehensive fundamental analysis

### 🚀 Current Status & Next Steps:
"""

            if summary.get('system_status') == 'production_ready':
                report += """
**🎉 PRODUCTION READY!** Your trading system is fully operational:

1. **Deploy Backend API** - All data infrastructure is ready
2. **Launch Frontend Dashboard** - Breakout data is being generated
3. **Activate ML Models** - Training data is comprehensive
4. **Enable Live Trading** - System monitoring is active
5. **Scale Operations** - Add more symbols or reduce update intervals
"""
            else:
                report += """
**⚠️ SYSTEM NEEDS ATTENTION:**

1. **Data Collection** - Ensure all updaters are running daily
2. **Quality Enhancement** - Run fundamentals updates for missing scores  
3. **ML Data Generation** - Execute historical breakouts generator
4. **System Monitoring** - Check error logs and performance metrics
"""

            # Configuration summary
            report += f"""
### ⚙️ Configuration Summary:
- **Database:** {config.db_host}:{config.db_port}/{config.db_name}
- **Screening Criteria:** Min Volume: {format_number(config.min_volume)}, Price Range: ${config.min_price}-{config.max_price}
- **ML Config:** {config.ml_lookforward_days} days lookforward, {config.ml_min_data_points} min data points
- **Rate Limiting:** {config.rate_limit_delay}s delay, {config.max_concurrent_updates} max concurrent
- **Data Directories:** Reports: {config.reports_dir}, Frontend: {config.frontend_data_dir}

---
*Report generated by Trading System Orchestrator v2.0*
*System uptime: {(datetime.now() - self.start_time).total_seconds():.0f} seconds*
"""

            return report

        except Exception as e:
            logger.error(f"Error generating comprehensive report: {e}")
            return f"Error generating report: {e}"

    @timing_decorator()
    def save_report(self, content: str, filename: str = None) -> Optional[str]:
        """Save report to file with enhanced naming"""
        try:
            if filename is None:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"trading_system_report_{timestamp}.md"

            filepath = os.path.join(self.reports_dir, filename)
            success = file_utils.save_json(content, filepath.replace('.md', '.txt'))  # Save as text

            # Also save as markdown
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)

            if success:
                logger.info(f"📄 Report saved: {filepath}")

                # Clean up old reports (keep last 30 days)
                cleanup_count = file_utils.cleanup_old_files(
                    self.reports_dir, days_old=30, pattern="trading_system_report_*.md"
                )
                if cleanup_count > 0:
                    logger.info(f"🧹 Cleaned up {cleanup_count} old reports")

                return filepath
            else:
                logger.error(f"❌ Failed to save report")
                return None

        except Exception as e:
            logger.error(f"❌ Failed to save report: {e}")
            return None

    def run_component(self, component_name: str, args: List[str] = None) -> Dict[str, Any]:
        """Run a specific trading system component"""
        try:
            # Component mapping
            components = {
                'daily_data': 'python mechanism/data_updaters/daily_data_updater.py',
                'fundamentals': 'python mechanism/data_updaters/fundamentals_updater.py',
                'screener': 'python mechanism/screeners/donchian_screener.py',
                'ml_generator': 'python mechanism/ml_generators/historical_breakouts_generator.py'
            }

            if component_name not in components:
                return {
                    'success': False,
                    'error': f"Unknown component: {component_name}. Available: {list(components.keys())}"
                }

            command = components[component_name].split()
            if args:
                command.extend(args)

            logger.info(f"🚀 Running component: {component_name}")
            logger.info(f"Command: {' '.join(command)}")

            start_time = time.time()
            result = subprocess.run(command, capture_output=True, text=True, timeout=3600)  # 1 hour timeout
            duration = time.time() - start_time

            return {
                'success': result.returncode == 0,
                'return_code': result.returncode,
                'duration_seconds': duration,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'command': ' '.join(command)
            }

        except subprocess.TimeoutExpired:
            logger.error(f"❌ Component {component_name} timed out after 1 hour")
            return {
                'success': False,
                'error': 'Component execution timed out',
                'timeout': True
            }
        except Exception as e:
            logger.error(f"❌ Error running component {component_name}: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    @timing_decorator()
    def run_daily_workflow(self, components: List[str] = None) -> Dict[str, Any]:
        """Execute complete daily workflow with all components"""
        start_time = datetime.now()
        logger.info(f"🚀 Starting daily workflow at {start_time}")

        # Default workflow components
        if components is None:
            components = [
                ('daily_data', ['--limit', '500']),  # Limit for safety
                ('fundamentals', ['--limit', '100']),
                ('screener', []),
                ('ml_generator', ['--limit', '50'])
            ]

        workflow_results = {}
        overall_success = True

        for i, (component_name, args) in enumerate(components, 1):
            logger.info(f"\n📊 Step {i}/{len(components)}: {component_name}")

            result = self.run_component(component_name, args)
            workflow_results[component_name] = result

            if result['success']:
                logger.info(f"✅ {component_name} completed successfully ({result['duration_seconds']:.1f}s)")
            else:
                logger.error(f"❌ {component_name} failed: {result.get('error', 'Unknown error')}")
                overall_success = False

                # Log stderr if available
                if result.get('stderr'):
                    logger.error(f"Error output: {result['stderr'][:500]}...")

        # Generate final report
        logger.info("\n📊 Generating workflow completion report...")
        final_report = self.generate_comprehensive_report()
        report_path = self.save_report(final_report)

        total_duration = datetime.now() - start_time

        logger.info(f"""
        🎉 Daily workflow completed in {total_duration}:
        📊 Components run: {len(components)}
        ✅ Successful: {sum(1 for r in workflow_results.values() if r['success'])}
        ❌ Failed: {sum(1 for r in workflow_results.values() if not r['success'])}
        📄 Report saved: {report_path or 'Failed to save'}
        """)

        return {
            'success': overall_success,
            'total_duration_seconds': total_duration.total_seconds(),
            'components_run': len(components),
            'component_results': workflow_results,
            'report_path': report_path,
            'summary': {
                'successful_components': sum(1 for r in workflow_results.values() if r['success']),
                'failed_components': sum(1 for r in workflow_results.values() if not r['success'])
            }
        }


def main():
    """Main function with enhanced command line interface"""
    parser = argparse.ArgumentParser(description='Enhanced Trading System Orchestrator')
    parser.add_argument('command', choices=[
        'health', 'report', 'breakouts', 'workflow', 'run-component'
    ], help='Command to run')

    parser.add_argument('--component', type=str, help='Component name for run-component command')
    parser.add_argument('--args', nargs='*', help='Arguments for component')
    parser.add_argument('--save-report', action='store_true', help='Save report to file')
    parser.add_argument('--days', type=int, default=30, help='Days for breakouts analysis')

    args = parser.parse_args()

    orchestrator = TradingSystemOrchestrator()

    if args.command == 'health':
        logger.info("🏥 Running comprehensive health check...")
        health = orchestrator.run_comprehensive_health_check()

        # Determine exit code based on system health
        core_ready = health['system_summary'].get('core_healthy', False)
        sys.exit(0 if core_ready else 1)

    elif args.command == 'report':
        logger.info("📊 Generating comprehensive system report...")
        report = orchestrator.generate_comprehensive_report()
        print(report)

        if args.save_report:
            orchestrator.save_report(report)

        sys.exit(0)

    elif args.command == 'breakouts':
        logger.info(f"🚀 Getting top breakouts ({args.days} days)...")
        breakouts = orchestrator.get_top_breakouts(20, args.days)

        if breakouts:
            print(f"\n🚀 TOP BREAKOUTS (Last {args.days} days):")
            print("=" * 80)

            for i, breakout in enumerate(breakouts, 1):
                success_icon = "✅" if breakout['success'] else "❌"
                current_perf = f" | Current: {breakout['current_performance_pct']:+.1f}%" if breakout[
                    'current_performance_pct'] else ""

                print(f"{i:2d}. {breakout['symbol']} ({breakout['breakout_type']}) {success_icon}")
                print(
                    f"    Entry: ${breakout['entry_price']:.2f} | Max Gain: {breakout['max_gain_10d']:.1f}%{current_perf}")
                print(f"    Quality: {breakout['quality_grade']} | Sector: {breakout.get('sector', 'N/A')}")
                print()
        else:
            print("No breakouts found in the specified time period.")

        sys.exit(0)

    elif args.command == 'workflow':
        logger.info("🚀 Running complete daily workflow...")
        result = orchestrator.run_daily_workflow()

        if result['success']:
            logger.info("🎉 Daily workflow completed successfully!")
        else:
            logger.error("❌ Daily workflow completed with failures")

        sys.exit(0 if result['success'] else 1)

    elif args.command == 'run-component':
        if not args.component:
            logger.error("❌ Component name required for run-component command")
            sys.exit(1)

        logger.info(f"🚀 Running component: {args.component}")
        result = orchestrator.run_component(args.component, args.args or [])

        if result['success']:
            logger.info(f"✅ Component {args.component} completed successfully")
            if result.get('stdout'):
                print(result['stdout'])
        else:
            logger.error(f"❌ Component {args.component} failed")
            if result.get('stderr'):
                print(f"Error: {result['stderr']}")

        sys.exit(0 if result['success'] else 1)


if __name__ == "__main__":
    main()