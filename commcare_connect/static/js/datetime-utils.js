// Solicitations: Convert UTC timestamps to user's local timezone
// Used in solicitation response forms, success pages, and draft lists
function convertTimestamps() {
  var elements = document.querySelectorAll('.local-datetime');
  for (var i = 0; i < elements.length; i++) {
    var element = elements[i];
    var isoDateTime = element.getAttribute('datetime');
    if (isoDateTime) {
      var date = new Date(isoDateTime);
      var localTime = date.toLocaleString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        hour12: true,
      });
      element.textContent = localTime;
    }
  }
}

// Auto-run when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', convertTimestamps);
} else {
  convertTimestamps();
}
