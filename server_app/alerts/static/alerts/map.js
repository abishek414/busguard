// Map helpers (Leaflet + OpenStreetMap, free, no API key needed).

const DARWIN_CENTRE = [-12.43, 130.89];
const COLOURS = { green: "#198754", red: "#dc3545", orange: "#fd7e14", blue: "#0d6efd", grey: "#6c757d" };

function makeMap(elementId, zoom = 12) {
  const map = L.map(elementId).setView(DARWIN_CENTRE, zoom);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  return map;
}

// Keeps a set of dots on the map in sync with a list from the server.
class DotLayer {
  constructor(map) {
    this.map = map;
    this.dots = {};
  }

  // items: [{key, lat, lon, colour, label, radius, permanent, onClick}]
  update(items) {
    const seen = new Set();
    for (const it of items) {
      if (it.lat == null || it.lon == null) continue;
      seen.add(it.key);
      const style = { radius: it.radius || 8, color: "#fff", weight: 2,
                      fillColor: COLOURS[it.colour] || it.colour, fillOpacity: 0.95 };
      let dot = this.dots[it.key];
      if (!dot) {
        dot = L.circleMarker([it.lat, it.lon], style).addTo(this.map);
        dot.bindTooltip(it.label, { permanent: !!it.permanent, direction: "top", offset: [0, -6] });
        if (it.onClick) dot.on("click", it.onClick);
        this.dots[it.key] = dot;
      } else {
        dot.setLatLng([it.lat, it.lon]);
        dot.setStyle(style);
        dot.setRadius(style.radius);
        dot.setTooltipContent(it.label);
      }
    }
    for (const key of Object.keys(this.dots)) {
      if (!seen.has(key)) {
        this.dots[key].remove();
        delete this.dots[key];
      }
    }
  }
}

// Straight-line distance in km between two points
function distanceKm(lat1, lon1, lat2, lon2) {
  const rad = d => d * Math.PI / 180;
  const a = Math.sin(rad(lat2 - lat1) / 2) ** 2 +
            Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(rad(lon2 - lon1) / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(a));
}
