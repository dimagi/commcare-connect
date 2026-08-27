import Alpine from 'alpinejs';
import Persist from '@alpinejs/persist';
import Tooltip from '@ryangjchandler/alpine-tooltip';
import './alpine_data';
Alpine.plugin(Persist);
// Interactive tooltips get clipped in overflow-hidden containers, since Tippy
// only appends to <body> if appendTo differs by reference from its default —
// which a global override can't do. Set it per-instance instead.
Tooltip.defaultProps({
  onCreate(instance) {
    if (instance.props.interactive) {
      instance.setProps({ appendTo: () => document.body });
    }
  },
});
Alpine.plugin(Tooltip);
window.Alpine = Alpine;
window.Alpine.start();
import 'tippy.js/dist/tippy.css';
