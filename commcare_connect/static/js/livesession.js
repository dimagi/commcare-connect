window['__ls_namespace'] = '__ls';
window['__ls_script_url'] = 'https://cdn.livesession.io/track.js';
!(function (w, d, t, u, n) {
  if (n in w) {
    if (w.console && w.console.log) {
      w.console.log(
        'LiveSession namespace conflict. Please set window["__ls_namespace"].',
      );
    }
    return;
  }
  if (w[n]) return;
  var f = (w[n] = function () {
    f.push ? f.push.apply(f, arguments) : f.store.push(arguments);
  });
  if (!w[n]) w[n] = f;
  f.store = [];
  f.v = '1.1';

  var ls = d.createElement(t);
  ls.async = true;
  ls.src = u;
  var s = d.getElementsByTagName(t)[0];
  s.parentNode.insertBefore(ls, s);
})(
  window,
  document,
  'script',
  window['__ls_script_url'],
  window['__ls_namespace'],
);

const liveSessionAppId = document.getElementById('tracker-data');
if (liveSessionAppId) {
  const trackerData = JSON.parse(liveSessionAppId.textContent);
  __ls('init', trackerData.app_id, { keystrokes: false });
  __ls('newPageView');
}
