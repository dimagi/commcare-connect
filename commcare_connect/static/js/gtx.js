(() => {
  // === CONFIG ===
  const ALLOWED_TAG_TYPES = ['google'];
  const BLOCKED_TAG_TYPES = ['jsm', 'html', 'img', 'j', 'k'];

  // === INIT DATA LAYER ===
  window.dataLayer = window.dataLayer || [];
  window.dataLayer.push({
    'gtm.start': new Date().getTime(),
    event: 'gtm.js',
  });

  const setAllowedTagTypes = () => {
    window.dataLayer.push({
      'gtm.allowlist': ALLOWED_TAG_TYPES,
      'gtm.blocklist': BLOCKED_TAG_TYPES,
    });
  };

  const gtmSendEvent = (eventName, eventData = {}, callbackFn) => {
    const data = { event: eventName, ...eventData };
    window.dataLayer.push(data);
    if (typeof callbackFn === 'function') callbackFn();
  };

  const initializeGTM = () => {
    const gtmDataScript = document.getElementById('gtm-data');
    if (gtmDataScript) {
      const gtmData = JSON.parse(gtmDataScript.textContent);
      if (gtmData.gtmID) {
        window.dataLayer.push({
          isDimagi: gtmData.isDimagi,
        });
        loadGTM(gtmData.gtmID);
      }
    }
  };

  const loadGTM = (id) => {
    const script = document.createElement('script');
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtm.js?id=${id}`;
    document.head.appendChild(script);
  };

  // === INIT ===
  setAllowedTagTypes();
  initializeGTM();

  // === GLOBAL EXPOSURE ===
  window.gtmSendEvent = gtmSendEvent;
})();
