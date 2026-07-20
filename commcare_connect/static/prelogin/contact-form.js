/* global hbspt */
(function () {
  function mountForm() {
    if (typeof hbspt === 'undefined' || !hbspt.forms) return false;
    hbspt.forms.create({
      region: 'na1',
      portalId: '503070',
      formId: 'ca08edba-5d8f-4386-b5e9-d6b026c14599',
      target: '#hubspot-form',
      onFormReady: function () {
        var fb = document.getElementById('hubspot-form-fallback');
        if (fb && fb.parentNode) fb.parentNode.removeChild(fb);
      },
    });
    return true;
  }
  if (!mountForm()) {
    var s = document.querySelector('script[src*="js.hsforms.net"]');
    if (s) s.addEventListener('load', mountForm);
  }
})();
