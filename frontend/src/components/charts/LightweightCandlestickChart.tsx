'use client';

import React, { useEffect, useRef } from 'react';
import {
  createChart,
  ChartOptions,
  DeepPartial,
  IChartApi,
  ISeriesApi,
  UTCTimestamp,
  SeriesMarker,
  Time,
} from 'lightweight-charts';
import { PriceBar } from '@/services/api';
import { nearestBarDate } from '@/lib/priceMath';

export interface EarningsMarker {
  date: string; // YYYY-MM-DD
  eps_estimate?: number | null;
  eps_actual?: number | null;
}

export interface LightweightCandlestickChartProps {
  bars: PriceBar[];
  earnings?: EarningsMarker[];
  variant?: 'full' | 'mini';
  height?: number;
  showDonchian?: boolean;
  showVolume?: boolean;
  className?: string;
}

// Soft slate tint (slate-100) so the plot area separates from the white card /
// popover around it. Grid lines are one step darker (slate-200 would be
// near-invisible on this fill).
const CHART_BG = '#f1f5f9';
const CHART_GRID = '#dde4ee';

/** Chart options that differ between the full chart and the hover-card mini chart. */
function variantOptions(isMini: boolean): DeepPartial<ChartOptions> {
  return {
    grid: {
      vertLines: { visible: !isMini, color: CHART_GRID },
      horzLines: { visible: !isMini, color: CHART_GRID },
    },
    rightPriceScale: {
      visible: !isMini,
      borderVisible: !isMini,
    },
    timeScale: {
      visible: !isMini,
      borderVisible: !isMini,
      timeVisible: false,
    },
    crosshair: {
      vertLine: { visible: !isMini, labelVisible: !isMini },
      horzLine: { visible: !isMini, labelVisible: !isMini },
    },
    handleScroll: !isMini,
    handleScale: !isMini,
  };
}

function toTime(dateStr: string): UTCTimestamp {
  // Bars are plain "YYYY-MM-DD" trading dates -- treat as UTC midnight so
  // lightweight-charts' business-day handling doesn't shift them a day off
  // depending on the viewer's timezone.
  return (Date.parse(`${dateStr}T00:00:00Z`) / 1000) as UTCTimestamp;
}

export default function LightweightCandlestickChart({
  bars,
  earnings = [],
  variant = 'full',
  height,
  showDonchian = true,
  showVolume = variant === 'full',
  className,
}: LightweightCandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const donchianHighRef = useRef<ISeriesApi<'Line'> | null>(null);
  const donchianLowRef = useRef<ISeriesApi<'Line'> | null>(null);

  const resolvedHeight = height ?? (variant === 'mini' ? 110 : 380);
  const isMini = variant === 'mini';

  // The chart is created once, with the variant/height the component mounted
  // with; later changes are applied by the effect below (destroying and
  // recreating the chart would drop its series data).
  const initialRef = useRef({ isMini, height: resolvedHeight });

  // Mount-only: create the chart + series once, tear down on unmount.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: initialRef.current.height,
      layout: {
        background: { color: CHART_BG },
        textColor: '#475569',
        fontSize: 10,
      },
      ...variantOptions(initialRef.current.isMini),
    });
    chartRef.current = chart;

    candleSeriesRef.current = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    donchianHighRef.current = chart.addLineSeries({
      color: '#94a3b8',
      lineWidth: 1,
      lineStyle: 2, // dashed
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });
    donchianLowRef.current = chart.addLineSeries({
      color: '#94a3b8',
      lineWidth: 1,
      lineStyle: 2,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });

    volumeSeriesRef.current = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      chart.applyOptions({ width: entry.contentRect.width });
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      donchianHighRef.current = null;
      donchianLowRef.current = null;
    };
  }, []);

  // Height/variant changes after mount (rare, but keep the chart consistent if they change).
  useEffect(() => {
    chartRef.current?.applyOptions({ ...variantOptions(isMini), height: resolvedHeight });
  }, [isMini, resolvedHeight]);

  // Data updates.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    const donchianHigh = donchianHighRef.current;
    const donchianLow = donchianLowRef.current;
    if (!candleSeries || !volumeSeries || !donchianHigh || !donchianLow) return;

    const validBars = bars.filter(
      (b) => b.open != null && b.high != null && b.low != null && b.close != null
    );

    candleSeries.setData(
      validBars.map((b) => ({
        time: toTime(b.date),
        open: b.open as number,
        high: b.high as number,
        low: b.low as number,
        close: b.close,
      }))
    );

    if (showVolume) {
      volumeSeries.setData(
        bars
          .filter((b) => b.volume != null)
          .map((b) => ({
            time: toTime(b.date),
            value: b.volume as number,
            color: b.close >= (b.open ?? b.close) ? '#10b98166' : '#ef444466',
          }))
      );
    } else {
      volumeSeries.setData([]);
    }

    if (showDonchian) {
      donchianHigh.setData(
        bars
          .filter((b) => b.donchian_high_20 != null)
          .map((b) => ({ time: toTime(b.date), value: b.donchian_high_20 as number }))
      );
      donchianLow.setData(
        bars
          .filter((b) => b.donchian_low_20 != null)
          .map((b) => ({ time: toTime(b.date), value: b.donchian_low_20 as number }))
      );
    } else {
      donchianHigh.setData([]);
      donchianLow.setData([]);
    }

    chartRef.current?.timeScale().fitContent();
  }, [bars, showDonchian, showVolume]);

  // Earnings markers.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    if (!candleSeries) return;

    if (!earnings.length || !bars.length) {
      candleSeries.setMarkers([]);
      return;
    }

    const markers: SeriesMarker<Time>[] = [];
    for (const e of earnings) {
      const snapped = nearestBarDate(bars, e.date, 3);
      if (!snapped) continue;
      markers.push({
        time: toTime(snapped),
        position: 'aboveBar',
        color: '#7c3aed',
        shape: 'circle',
        text: 'E',
      });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    candleSeries.setMarkers(markers);
  }, [earnings, bars]);

  return (
    <div
      ref={containerRef}
      className={className}
      style={{ width: '100%', height: resolvedHeight, borderRadius: 6, overflow: 'hidden' }}
    />
  );
}
