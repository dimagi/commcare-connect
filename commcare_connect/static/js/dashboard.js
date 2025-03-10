console.log('dashboard.js loaded');

// colors to use for the categories
// soft green, yellow, red
const visitColors = ['#4ade80', '#fbbf24', '#f87171'];

// after the GeoJSON data is loaded, update markers on the screen on every frame
// objects for caching and keeping track of HTML marker objects (for performance)
const markers = {};
let markersOnScreen = {};

function updateMarkers(map) {
  const newMarkers = {};
  const features = map.querySourceFeatures('visits');

  // for every cluster on the screen, create an HTML marker for it (if we didn't yet),
  // and add it to the map if it's not there already
  for (const feature of features) {
    const coords = feature.geometry.coordinates;
    const props = feature.properties;
    if (!props.cluster) continue;
    const id = props.cluster_id;

    let marker = markers[id];
    if (!marker) {
      const el = createDonutChart(
        {
          ...props,
          cluster_id: id, // Make sure cluster_id is passed
          coordinates: coords, // Pass the coordinates
        },
        map,
      );
      marker = markers[id] = new mapboxgl.Marker({
        element: el,
      }).setLngLat(coords);
    }
    newMarkers[id] = marker;

    if (!markersOnScreen[id]) marker.addTo(map);
  }
  // for every marker we've added previously, remove those that are no longer visible
  for (const id in markersOnScreen) {
    if (!newMarkers[id]) markersOnScreen[id].remove();
  }
  markersOnScreen = newMarkers;
}

// Function to create a donut chart
function createDonutChart(props, map) {
  const offsets = [];
  const counts = [props.approved, props.pending, props.rejected];
  let total = 0;
  for (const count of counts) {
    offsets.push(total);
    total += count;
  }
  const fontSize =
    total >= 1000 ? 22 : total >= 100 ? 20 : total >= 10 ? 18 : 16;
  const r = total >= 1000 ? 50 : total >= 100 ? 32 : total >= 10 ? 24 : 18;
  const r0 = Math.round(r * 0.8);
  const w = r * 2;

  let html = `<div>
        <svg width="${w}" height="${w}" viewbox="0 0 ${w} ${w}" text-anchor="middle" style="font: ${fontSize}px sans-serif; display: block">
        <defs>
          <filter id="shadow">
            <feDropShadow dx="0" dy="1" stdDeviation="2" flood-opacity="0.3"/>
          </filter>
        </defs>`;

  for (let i = 0; i < counts.length; i++) {
    html += donutSegment(
      offsets[i] / total,
      (offsets[i] + counts[i]) / total,
      r,
      r0,
      visitColors[i],
    );
  }
  html += `<circle cx="${r}" cy="${r}" r="${r0}" fill="#374151" />
        <text dominant-baseline="central" transform="translate(${r}, ${r})"
              fill="white" font-weight="500" filter="url(#shadow)">
            ${total.toLocaleString()}
        </text>
        </svg>
        </div>`;

  const el = document.createElement('div');
  el.innerHTML = html;
  el.style.cursor = 'pointer';

  // Click handler to zoom and navigate to the cluster
  el.addEventListener('click', (e) => {
    map
      .getSource('visits')
      .getClusterExpansionZoom(props.cluster_id, (err, zoom) => {
        if (err) return;

        map.easeTo({
          center: props.coordinates,
          zoom: zoom,
        });
      });
  });

  return el;
}

// Function to create a donut segment
function donutSegment(start, end, r, r0, color) {
  if (end - start === 1) end -= 0.00001;
  const a0 = 2 * Math.PI * (start - 0.25);
  const a1 = 2 * Math.PI * (end - 0.25);
  const x0 = Math.cos(a0),
    y0 = Math.sin(a0);
  const x1 = Math.cos(a1),
    y1 = Math.sin(a1);
  const largeArc = end - start > 0.5 ? 1 : 0;

  // draw an SVG path
  return `<path d="M ${r + r0 * x0} ${r + r0 * y0} L ${r + r * x0} ${
    r + r * y0
  } A ${r} ${r} 0 ${largeArc} 1 ${r + r * x1} ${r + r * y1} L ${r + r0 * x1} ${
    r + r0 * y1
  } A ${r0} ${r0} 0 ${largeArc} 0 ${r + r0 * x0} ${
    r + r0 * y0
  }" fill="${color}" opacity="0.85" stroke="#1f2937" stroke-width="1" />`;
}

const chartColors = [
  { border: 'rgb(75, 192, 192)', background: 'rgba(75, 192, 192, 0.8)' },
  { border: 'rgb(255, 99, 132)', background: 'rgba(255, 99, 132, 0.8)' },
  { border: 'rgb(255, 205, 86)', background: 'rgba(255, 205, 86, 0.8)' },
  { border: 'rgb(54, 162, 235)', background: 'rgba(54, 162, 235, 0.8)' },
];

const statusColors = {
  approved: {
    background: 'rgba(74, 222, 128, 0.8)',
    border: 'rgb(74, 222, 128)',
  },
  rejected: {
    background: 'rgba(248, 113, 113, 0.8)',
    border: 'rgb(248, 113, 113)',
  },
  pending: {
    background: 'rgba(251, 191, 36, 0.8)',
    border: 'rgb(251, 191, 36)',
  },
};

function createTimeSeriesChart(ctx, data) {
  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels: data.labels,
      datasets: data.datasets.map((dataset, index) => ({
        label: dataset.name,
        data: dataset.data,
        borderColor: chartColors[index % chartColors.length].border,
        backgroundColor: chartColors[index % chartColors.length].background,
        borderWidth: 1,
      })),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          mode: 'index',
          intersect: false,
        },
      },
      scales: {
        x: {
          stacked: true,
          title: {
            display: true,
            text: 'Date',
          },
        },
        y: {
          stacked: true,
          beginAtZero: true,
          title: {
            display: true,
            text: 'Number of Visits',
          },
        },
      },
    },
  });
}

function createProgramPieChart(ctx, data) {
  // Check if there's no data or empty data
  if (!data?.data?.length) {
    return new Chart(ctx, {
      type: 'pie',
      data: {
        labels: ['No data'],
        datasets: [
          {
            data: [1],
            backgroundColor: ['rgba(156, 163, 175, 0.3)'],
            borderColor: ['rgb(156, 163, 175)'],
            borderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              boxWidth: 12,
              color: 'rgb(156, 163, 175)',
            },
          },
        },
      },
    });
  }

  return new Chart(ctx, {
    type: 'pie',
    data: {
      labels: data.labels,
      datasets: [
        {
          data: data.data,
          backgroundColor: chartColors.map((c) => c.background),
          borderColor: chartColors.map((c) => c.border),
          borderWidth: 1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            boxWidth: 12,
          },
        },
      },
    },
  });
}

function createStatusPieChart(ctx, data) {
  // Check if there's no data or empty data
  if (!data?.data?.length) {
    return new Chart(ctx, {
      type: 'pie',
      data: {
        labels: ['No data'],
        datasets: [
          {
            data: [1],
            backgroundColor: ['rgba(156, 163, 175, 0.3)'],
            borderColor: ['rgb(156, 163, 175)'],
            borderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              boxWidth: 12,
              color: 'rgb(156, 163, 175)',
            },
          },
        },
      },
    });
  }

  return new Chart(ctx, {
    type: 'pie',
    data: {
      labels: data.labels,
      datasets: [
        {
          data: data.data,
          backgroundColor: data.labels.map(
            (status) =>
              statusColors[status]?.background || 'rgba(156, 163, 175, 0.8)',
          ),
          borderColor: data.labels.map(
            (status) => statusColors[status]?.border || 'rgb(156, 163, 175)',
          ),
          borderWidth: 1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            boxWidth: 12,
          },
        },
      },
    },
  });
}

window.updateMarkers = updateMarkers;
window.createDonutChart = createDonutChart;
window.createTimeSeriesChart = createTimeSeriesChart;
window.createProgramPieChart = createProgramPieChart;
window.createStatusPieChart = createStatusPieChart;
