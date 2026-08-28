import Alpine from 'alpinejs';
import Persist from '@alpinejs/persist';
import Tooltip from '@ryangjchandler/alpine-tooltip';
import './alpine_data';
Alpine.plugin(Persist);
// Interactive tooltips get clipped in overflow-hidden containers, since Tippy
// only appends to <body> if appendTo differs by reference from its default —
// which a global override can't do. Set it per-instance instead.
//
// Scoped to the "dark" theme (our rich tooltips, e.g. the exchange-rate one) rather
// than all interactive tooltips: the pre-existing interactive tooltip in
// opportunity_resource_modal.html relies on staying inside the modal's DOM so the
// modal's `x-show`/`@click.away` naturally hides it on close. Reparenting it to
// <body> too would let it float on screen after the modal closes (a click inside the
// tooltip is "outside" the modal wrapper as far as `@click.away` is concerned, but
// Tippy itself won't hide an interactive tooltip on a click inside its own content).
Tooltip.defaultProps({
  onCreate(instance) {
    if (instance.props.interactive && instance.props.theme === 'dark') {
      instance.setProps({ appendTo: () => document.body });
    }
  },
});
Alpine.plugin(Tooltip);
window.Alpine = Alpine;
window.Alpine.start();
import 'tippy.js/dist/tippy.css';
