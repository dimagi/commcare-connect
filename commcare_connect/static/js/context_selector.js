// State for the header's organization / program / opportunity selector
// (templates/layouts/context_selector.html). The tree comes from the URL in `data-url`
// the first time the panel opens. `data-org`, `data-program` and `data-opportunity` hold
// the ids of the page's own context, so the panel opens with them selected.
function contextSelector() {
  return {
    open: false,
    status: 'idle', // idle | loading | loaded | error
    organizations: [],
    query: '',
    selected: { org: null, program: null, opportunity: null },

    toggle() {
      this.open = !this.open;
      if (this.open && ['idle', 'error'].includes(this.status)) this.load();
    },

    async load() {
      this.status = 'loading';
      try {
        const response = await fetch(this.$root.dataset.url, {
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) throw new Error(response.statusText);
        this.organizations = (await response.json()).organizations;
        this.selectCurrent();
        this.status = 'loaded';
      } catch {
        this.status = 'error';
      }
    },

    selectCurrent() {
      const current = this.$root.dataset;
      const org = this.organizations.find((o) => o.id === current.org);
      if (!org) return;
      const program =
        org.programs.find((p) => p.id === current.program) || null;
      const opportunity =
        program?.opportunities.find((o) => o.id === current.opportunity) ||
        null;
      this.selected = { org, program, opportunity };
    },

    matches(item) {
      return item.name.toLowerCase().includes(this.query.trim().toLowerCase());
    },

    programMatches(program) {
      return (
        this.matches(program) ||
        program.opportunities.some((o) => this.matches(o))
      );
    },

    get orgList() {
      return this.organizations.filter(
        (org) =>
          this.matches(org) || org.programs.some((p) => this.programMatches(p)),
      );
    },

    get programList() {
      const { org } = this.selected;
      if (!org) return [];
      return org.programs.filter(
        (p) => this.matches(org) || this.programMatches(p),
      );
    },

    get opportunityList() {
      const { org, program } = this.selected;
      if (!program) return [];
      return program.opportunities.filter(
        (o) => this.matches(org) || this.matches(program) || this.matches(o),
      );
    },

    get selectedPath() {
      const { org, program, opportunity } = this.selected;
      return [org, program, opportunity].filter(Boolean);
    },

    // The deepest selected item: where Apply goes.
    get target() {
      return this.selectedPath.at(-1);
    },

    get seeAllUrl() {
      const { org, program } = this.selected;
      return (program || org)?.opportunities_url;
    },

    selectOrg(org) {
      this.selected = { org, program: null, opportunity: null };
    },

    selectProgram(program) {
      this.selected = { ...this.selected, program, opportunity: null };
    },

    selectOpportunity(opportunity) {
      this.selected = { ...this.selected, opportunity };
    },

    clear() {
      this.selectOrg(null);
      this.query = '';
    },

    apply() {
      if (this.target) this.go(this.target);
    },

    // Double-clicking a row opens it, as Apply would.
    go(item) {
      window.location.href = item.url;
    },
  };
}
window.contextSelector = contextSelector;
