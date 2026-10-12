/* Marks which download fits this device and draws the pet. No tracking, no network requests. */
(function () {
  var ua = navigator.userAgent || '';
  document.body.setAttribute('data-platform', /Android/i.test(ua) ? 'android' : /iPhone|iPad|iPod/i.test(ua) ? 'ios' : 'desktop');
  var A = window.AlberichAssets, m = A.moods.happy, parts = [];
  m.rows.forEach(function (row, y) { for (var x = 0; x < row.length; x++) if (row[x] !== '.' && m.palette[row[x]]) parts.push('<rect x="' + x + '" y="' + y + '" width="1" height="1" fill="' + m.palette[row[x]] + '"/>'); });
  document.getElementById('pet').innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="alberich bob" width="' + m.rows[0].length * 8 + '" height="' + m.rows.length * 8 + '" viewBox="0 0 ' + m.rows[0].length + ' ' + m.rows.length + '" shape-rendering="crispEdges" role="img" aria-label="Alberich">' + parts.join('') + '</svg>';
})();
