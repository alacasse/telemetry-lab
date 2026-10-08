# Thermostat behavior

The launcher creates a simulated room backed by PostgreSQL. COOL requests `cooling`; HEAT requests `heating`. The requested mode is distinct from the actuator state in the last processed measurement.

Setpoints range from 15 to 30 °C inclusive in 0.5 °C increments. Settings are stored with a revision and operation identity. The mode selector reflects the recorded requested mode, and controls are blocked while a setting request or unresolved transition is in progress. Selecting the already recorded mode without changing the setpoint produces no new setting request.

The simulated temperature changes while an actuator is active. The controller requests a stop once the setpoint condition is reached. A processed measurement can overshoot the target; the display retains that measured value. There is no ambient drift, automatic mode switch, hysteresis or repeated maintenance cycle.

## Stop before inversion

Changing from heating to cooling, or from cooling to heating, requires proof that the previous action stopped. Publishing a stop command alone does not establish that proof. The transition follows the command through publication, simulator application and the associated processed measurement. Missing or stale proof keeps continuation blocked.

After an interruption, the runtime recovers stored sessions before creating defaults. A page reload reads the current room and settings without starting an action. Resume, stop and retry controls are explicit requests. An uncertain response must be resolved using stored operation and command evidence before proceeding.

These guards explain an asynchronous state machine using synthetic actuator states. They are not a physical equipment safety certification.
