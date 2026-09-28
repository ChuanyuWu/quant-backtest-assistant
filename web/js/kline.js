/* ECharts K线渲染器：蜡烛图 + 成交量 + MACD/KDJ副图 + 信号标记 + MA/BOLL叠加 */
class KlineChart {
  constructor(domId) {
    this.chart = echarts.init(document.getElementById(domId));
    if (typeof registerChart === 'function') registerChart(this.chart);
    window.addEventListener('resize', () => this.chart.resize());
  }

  /**
   * data: /api/kline 返回的数据
   * opts: { subIndicator: 'MACD'|'KDJ', showBoll: bool }
   */
  render(data, opts = {}) {
    const sub = opts.subIndicator || 'MACD';
    const dates = data.dates;

    // 买卖信号标记
    const buyPoints = data.buy_signals.map(i => ({
      coord: [dates[i], data.ohlc[i][2] * 0.985],
      value: '买', itemStyle: { color: '#ef4444' }
    }));
    const sellPoints = data.sell_signals.map(i => ({
      coord: [dates[i], data.ohlc[i][3] * 1.015],
      value: '卖', itemStyle: { color: '#10b981' }
    }));

    const maSeries = ['MA5', 'MA10', 'MA20'].map((k, idx) => ({
      name: k, type: 'line', data: data.ma[k], smooth: true,
      showSymbol: false, lineStyle: { width: 1.2 },
      itemStyle: { color: ['#f59e0b', '#8b5cf6', '#3b82f6'][idx] },
      xAxisIndex: 0, yAxisIndex: 0
    }));

    const bollSeries = opts.showBoll ? [
      { name: 'BOLL上轨', data: data.boll.upper, color: '#94a3b8' },
      { name: 'BOLL中轨', data: data.boll.mid, color: '#f59e0b' },
      { name: 'BOLL下轨', data: data.boll.lower, color: '#94a3b8' }
    ].map(s => ({
      name: s.name, type: 'line', data: s.data, showSymbol: false,
      lineStyle: { width: 1, type: 'dashed', color: s.color },
      itemStyle: { color: s.color }, xAxisIndex: 0, yAxisIndex: 0
    })) : [];

    const volColors = data.ohlc.map(k => k[1] >= k[0] ? '#ef4444' : '#10b981');

    let subSeries = [];
    const hasSub = (sub === 'MACD' && data.macd) || (sub === 'KDJ' && data.kdj);
    if (sub === 'MACD' && data.macd) {
      subSeries = [
        { name: 'DIF', type: 'line', data: data.macd.DIF, showSymbol: false, lineStyle: { width: 1.2 }, itemStyle: { color: '#3b82f6' }, xAxisIndex: 2, yAxisIndex: 2 },
        { name: 'DEA', type: 'line', data: data.macd.DEA, showSymbol: false, lineStyle: { width: 1.2 }, itemStyle: { color: '#f59e0b' }, xAxisIndex: 2, yAxisIndex: 2 },
        { name: 'MACD柱', type: 'bar', data: data.macd.bar.map(v => ({
            value: v, itemStyle: { color: v >= 0 ? '#ef4444' : '#10b981' }
          })), xAxisIndex: 2, yAxisIndex: 2 }
      ];
    } else if (sub === 'KDJ' && data.kdj) {
      subSeries = [
        { name: 'K', type: 'line', data: data.kdj.K, showSymbol: false, lineStyle: { width: 1.2 }, itemStyle: { color: '#3b82f6' }, xAxisIndex: 2, yAxisIndex: 2 },
        { name: 'D', type: 'line', data: data.kdj.D, showSymbol: false, lineStyle: { width: 1.2 }, itemStyle: { color: '#f59e0b' }, xAxisIndex: 2, yAxisIndex: 2 },
        { name: 'J', type: 'line', data: data.kdj.J, showSymbol: false, lineStyle: { width: 1.2 }, itemStyle: { color: '#8b5cf6' }, xAxisIndex: 2, yAxisIndex: 2 }
      ];
    }

    const option = {
      animation: false,
      legend: {
        data: ['K线', 'MA5', 'MA10', 'MA20', ...bollSeries.map(s => s.name), '成交量', ...subSeries.map(s => s.name)],
        top: 4, textStyle: { fontSize: 11 }
      },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross' },
        backgroundColor: 'rgba(255,255,255,0.95)', borderColor: '#e2e8f0',
        textStyle: { color: '#1e293b', fontSize: 12 },
        formatter: (params) => this._tooltip(params, data)
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: hasSub ? [
        { left: 60, right: 20, top: 32, height: '46%' },
        { left: 60, right: 20, top: '58%', height: '14%' },
        { left: 60, right: 20, top: '76%', height: '16%' }
      ] : [
        { left: 60, right: 20, top: 32, height: '58%' },
        { left: 60, right: 20, top: '72%', height: '18%' }
      ],
      xAxis: (hasSub ? [0, 1, 2] : [0, 1]).map(i => ({
        type: 'category', data: dates, gridIndex: i,
        axisLabel: { show: hasSub ? i === 2 : i === 1, fontSize: 10 },
        axisLine: { lineStyle: { color: '#e2e8f0' } }
      })),
      yAxis: (hasSub ? [
        { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: '#f1f5f9' } } },
        { scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { fontSize: 10 } },
        { scale: true, gridIndex: 2, splitLine: { lineStyle: { color: '#f1f5f9' } }, axisLabel: { fontSize: 10 } }
      ] : [
        { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: '#f1f5f9' } } },
        { scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { fontSize: 10 } }
      ]),
      dataZoom: [
        { type: 'inside', xAxisIndex: hasSub ? [0, 1, 2] : [0, 1], start: 60, end: 100 },
        { type: 'slider', xAxisIndex: hasSub ? [0, 1, 2] : [0, 1], bottom: 4, height: 18, start: 60, end: 100 }
      ],
      series: [
        {
          name: 'K线', type: 'candlestick', data: data.ohlc,
          itemStyle: {
            color: '#ef4444', color0: '#10b981',
            borderColor: '#ef4444', borderColor0: '#10b981'
          },
          xAxisIndex: 0, yAxisIndex: 0,
          markPoint: {
            symbol: 'triangle', symbolSize: 12,
            label: { show: false },
            data: [...buyPoints, ...sellPoints]
          }
        },
        ...maSeries, ...bollSeries,
        {
          name: '成交量', type: 'bar',
          data: data.volume.map((v, i) => ({ value: v, itemStyle: { color: volColors[i] } })),
          xAxisIndex: 1, yAxisIndex: 1
        },
        ...subSeries
      ]
    };
    this.chart.setOption(option, true);
  }

  _tooltip(params, data) {
    if (!params.length) return '';
    const idx = params[0].dataIndex;
    const k = data.ohlc[idx];
    const pct = idx > 0 ? ((k[1] - data.ohlc[idx - 1][1]) / data.ohlc[idx - 1][1] * 100).toFixed(2) : '--';
    let html = `<b>${data.dates[idx]}</b><br/>
      开 ${k[0]}　收 ${k[1]}　低 ${k[2]}　高 ${k[3]}<br/>
      涨跌 ${pct}%　量 ${(data.volume[idx] / 10000).toFixed(1)}万`;
    return html;
  }

  clear() { this.chart.clear(); }
}
