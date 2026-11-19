// Keeps a start/end date pair in order: the end picker starts the day after the start date,
// and the start picker stops the day before the end date.
function dateRange() {
  return {
    start: '',
    end: '',
    dayAfterStart() {
      return shiftDate(this.start, 1);
    },
    dayBeforeEnd() {
      return shiftDate(this.end, -1);
    },
  };
}

// Returns the YYYY-MM-DD date `days` away from `value`, or null to drop the limit when `value` is empty.
function shiftDate(value, days) {
  if (!value) {
    return null;
  }
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

window.dateRange = dateRange;
