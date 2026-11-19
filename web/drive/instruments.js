(() => {
  'use strict';

  const colors = {
    backdrop: '#121a2b',
    muted: '#aebcda',
    route: '#99b5ff',
    alert: '#ffaba0'
  };

  function surface(canvas) {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (width < 1 || height < 1) return null;

    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.scale(ratio, ratio);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    return { ctx, width, height };
  }

  function trace(ctx, points) {
    if (!points.length) return;
    ctx.beginPath();
    ctx.moveTo(...points[0]);
    for (const point of points.slice(1)) ctx.lineTo(...point);
  }

  function line(ctx, points, color, weight, dash = []) {
    trace(ctx, points);
    ctx.strokeStyle = color;
    ctx.lineWidth = weight;
    ctx.setLineDash(dash);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function dot(ctx, [x, y], radius, fill, stroke = fill, weight = 1) {
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.strokeStyle = stroke;
    ctx.lineWidth = weight;
    ctx.stroke();
  }

  function routePoint(route, km) {
    const points = route.polyline;
    const passage = Math.max(0, Math.min(1, km / route.distance_km));
    const legs = [0];
    const lonScale = Math.cos(points[0][0] * Math.PI / 180);
    for (let i = 1; i < points.length; i += 1) {
      const [lat, lon] = points[i];
      const [pastLat, pastLon] = points[i - 1];
      legs.push(legs[i - 1] + Math.hypot(lat - pastLat, (lon - pastLon) * lonScale));
    }

    const desired = legs.at(-1) * passage;
    const index = legs.findIndex(distance => distance >= desired);
    if (index < 1) return points[0];

    const ratio = (desired - legs[index - 1]) / (legs[index] - legs[index - 1]);
    return points[index - 1].map((coordinate, axis) => coordinate + (points[index][axis] - coordinate) * ratio);
  }

  function makeProjection(route, stops, width, height, inclination = 1) {
    const [latitude, longitude] = route.polyline[0];
    const cosine = Math.cos(latitude * Math.PI / 180);
    const coordinates = ([lat, lon]) => [(lon - longitude) * cosine, latitude - lat];
    const [lastX, lastY] = coordinates(route.polyline.at(-1));
    const rotation = Math.atan2(height * .63 * inclination, width * .82) - Math.atan2(lastY, lastX);
    function rotate(point) {
      const [x, y] = coordinates(point);
      return [x * Math.cos(rotation) - y * Math.sin(rotation), x * Math.sin(rotation) + y * Math.cos(rotation)];
    }

    const all = [...route.polyline, ...stops].map(rotate);
    const x = all.map(p => p[0]);
    const y = all.map(p => p[1]);
    const minX = Math.min(...x);
    const minY = Math.min(...y);
    const extentX = Math.max(...x) - minX;
    const extentY = Math.max(...y) - minY;
    const padding = Math.min(61, width * .12);
    const scale = Math.min((width - padding * 2) / extentX, (height - padding * 2) / extentY);
    const left = (width - extentX * scale) / 2;
    const top = (height - extentY * scale) / 2;
    return point => {
      const [px, py] = rotate(point);
      return [(px - minX) * scale + left, (py - minY) * scale + top];
    };
  }

  function drawJourney(canvas, release, reading = {}) {
    const view = surface(canvas);
    if (!view) return;
    const { ctx, width, height } = view;
    const { route, stops, alternates } = release;
    const { km = 0, forecast, outage } = reading;
    const backup = forecast?.divert_to ?? alternates.find(stop => stop.p_works >= .9);
    const locations = stops.map(stop => [stop.lat, stop.lon]);
    const anchors = backup ? [...locations, [backup.lat, backup.lon]] : locations;
    const project = makeProjection(route, anchors, width, height, -.86);
    const points = route.polyline.map(project);
    const current = project(routePoint(route, km));
    const markers = locations.map(project);
    const alternate = backup ? project([backup.lat, backup.lon]) : null;
    const failedIndex = outage || forecast ? stops.findIndex(stop => stop.charger_id === (reading.failedId || stops[0]?.charger_id)) : -1;
    const first = points[0];
    const last = points.at(-1);
    const deck = Math.max(21, Math.min(width * .063, height * .14));

    const wash = ctx.createRadialGradient(width * .58, height * .38, height * .07, width * .58, height * .43, width * .7);
    wash.addColorStop(0, '#263c62ed');
    wash.addColorStop(.48, '#101e34f9');
    wash.addColorStop(1, '#0a1425ff');
    ctx.fillStyle = wash;
    ctx.fillRect(0, 0, width, height);

    const horizon = height * .18;
    for (let column = -11; column <= 11; column += 1) {
      line(ctx, [[width * .46 + column * width * .035, horizon],
        [width * .46 + column * width * .13, height]], '#abc7fc14', 1);
    }
    for (let row = 1; row <= 13; row += 1) {
      const y = horizon + (height - horizon) * (row / 13) ** 1.62;
      line(ctx, [[0, y], [width, y]], '#abc7fc13', 1);
    }

    const shadow = points.map(([x, y]) => [x + 6, y + deck * .29]);
    ctx.save();
    ctx.shadowColor = '#030812d3';
    ctx.shadowBlur = deck * .58;
    line(ctx, shadow, '#04091299', deck + 10);
    ctx.restore();
    line(ctx, shadow, '#0b1422', deck + 3);
    line(ctx, points, '#536c96', deck + 2);
    line(ctx, points, '#1d2b44', deck);
    line(ctx, points, '#101c30', deck * .79);
    line(ctx, points, '#8a9ebe8a', 1.2, [8, 9]);

    let endIndex = 0;
    let closest = Infinity;
    for (let i = 0; i < points.length; i += 1) {
      const distance = Math.hypot(points[i][0] - current[0], points[i][1] - current[1]);
      if (distance < closest) {
        closest = distance;
        endIndex = i;
      }
    }

    ctx.save();
    ctx.shadowColor = '#7097ffef';
    ctx.shadowBlur = 14;
    line(ctx, [...points.slice(0, endIndex + 1), current], '#a5c1ff', 3.8);
    ctx.restore();

    const warm = '#ffad92';
    if (forecast?.divert_to && alternate) {
      const branch = Array.from({ length: 25 }, (_, i) => {
        const t = i / 24;
        return [current[0] + (alternate[0] - current[0]) * t,
          current[1] + (alternate[1] - current[1]) * t + Math.sin(Math.PI * t) * deck * .88];
      });
      line(ctx, branch, '#081221', deck * .45);
      line(ctx, branch, '#9ab8ff46', deck * .41);
      ctx.save();
      ctx.shadowColor = '#a8c7ffca';
      ctx.shadowBlur = 10;
      line(ctx, branch, '#b3d3ff', 2.5, [5, 4]);
      ctx.restore();
    }

    dot(ctx, first, 4.5, '#bdd1ff');
    dot(ctx, last, 5.4, '#111e35', '#a7b9df', 1.4);
    markers.forEach(([x, y], index) => {
      const failed = index === failedIndex;
      dot(ctx, [x, y], 6.4, failed ? '#482d2a' : '#29283a', warm, 1.4);
      if (failed) {
        line(ctx, [[x - 2.4, y - 2.4], [x + 2.4, y + 2.4]], warm, 1.7);
        line(ctx, [[x + 2.4, y - 2.4], [x - 2.4, y + 2.4]], warm, 1.7);
      }
    });
    if (alternate) {
      dot(ctx, alternate, 5.1, forecast ? '#abc7ff' : '#142138', '#b2ccff', 1.3);
    }
    dot(ctx, current, 12, '#88adff31', '#a0baff86', 1);
    dot(ctx, current, 4.1, '#f6f9ff');

    ctx.font = '11px "IBM Plex Mono", monospace';
    ctx.fillStyle = '#dce8ff';
    ctx.textAlign = 'left';
    ctx.fillText('NOIDA', Math.max(9, first[0] - 4), Math.min(height - 28, first[1] + deck * .65 + 10));
    ctx.textAlign = 'right';
    ctx.fillText('AGRA', Math.min(width - 10, last[0] + 7), Math.max(17, last[1] - deck * .65 - 8));

    if (width > 362) {
      const planned = markers[failedIndex < 0 ? 0 : failedIndex];
      if (planned) {
        const pl = [planned[0] - deck * .19, planned[1] - deck * 1.05];
        line(ctx, [planned, pl], '#e3a68b7d', 1);
        ctx.fillStyle = '#ffbaaa';
        ctx.fillText(failedIndex >= 0 ? 'CHARGER OFFLINE' : 'PLANNED CHARGER', Math.max(110, pl[0] - 5), pl[1] - 4);
      }
      if (forecast?.divert_to && alternate) {
        const al = [alternate[0] + deck * 1.2, alternate[1] + deck * .84];
        line(ctx, [alternate, al], '#9cbcfb94', 1);
        ctx.textAlign = 'left';
        ctx.fillStyle = '#c8ddff';
        ctx.fillText('ALTERNATE', Math.min(width - 70, al[0] + 5), al[1] + 4);
      }
    }
  }

  function nearestReading(release, km) {
    return release.bingo_line.reduce((nearest, reading) =>
      Math.abs(reading.km - km) < Math.abs(nearest.km - km) ? reading : nearest
    );
  }

  function drawForecast(canvas, release, km) {
    const view = surface(canvas);
    if (!view) return;
    const { ctx, width, height } = view;
    const data = release.bingo_line;
    const right = width - 13;
    const left = 36;
    const top = 11;
    const bottom = height - 24;
    const maxDistance = release.route.distance_km;
    const x = distance => left + distance / maxDistance * (right - left);
    const y = battery => bottom - battery / 100 * (bottom - top);
    const points = field => data.map(row => [x(row.km), y(row[field])]);

    ctx.font = '10px "IBM Plex Mono", monospace';
    ctx.fillStyle = colors.muted;
    ctx.textAlign = 'right';
    for (const battery of [0, 25, 50, 75, 100]) {
      line(ctx, [[left, y(battery)], [right, y(battery)]], '#354361', 1);
      ctx.fillText(battery + '%', left - 7, y(battery) + 3);
    }
    ctx.textAlign = 'center';
    for (const distance of [0, 50, 100, 150]) {
      line(ctx, [[x(distance), top], [x(distance), bottom]], '#2a364f', 1);
      ctx.fillText(distance + ' km', x(distance), bottom + 17);
    }

    const range = [...points('expected_soc_p90'), ...points('expected_soc_p10').reverse()];
    trace(ctx, range);
    ctx.closePath();
    ctx.fillStyle = '#99b5ff27';
    ctx.fill();
    line(ctx, points('expected_soc_pct'), colors.route, 2);
    line(ctx, points('bingo_soc_pct'), colors.alert, 1.5, [3, 4]);

    const reading = nearestReading(release, km);
    const position = x(reading.km);
    line(ctx, [[position, top], [position, bottom]], '#d9e4ff7a', 1.2, [2, 5]);
    dot(ctx, [position, y(reading.expected_soc_pct)], 4, colors.route, colors.backdrop, 1.4);
    dot(ctx, [position, y(reading.bingo_soc_pct)], 4, colors.alert, colors.backdrop, 1.4);
  }

  window.verityInstruments = { drawJourney, drawForecast, nearestReading };
})();
