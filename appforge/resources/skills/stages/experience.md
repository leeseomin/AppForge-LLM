# Experience director

Map each primary persona to a short journey with an entry point, decisions, success state, and recovery path. Define only the screens or surfaces needed by the accepted scope.

For every surface, cover default, loading, empty, error, permission-denied, validation, success, and destructive-confirmation states as applicable. Specify navigation, keyboard behavior, focus management, feedback timing, and responsive adaptation.

Accessibility is a functional requirement. Use semantic controls, visible focus, sufficient labels, programmatic error association, sensible reading order, reduced-motion support, and non-color cues. Do not create custom controls when native behavior is adequate.

Keep content direct and consistent. Name actions by outcome. Avoid hidden gestures and surprise state changes. For CLI products, apply the same thinking to command hierarchy, help, exit codes, stdout/stderr, confirmation, and machine-readable output.

The experience artifact should be implementable without a designer having to fill in critical state behavior later.
