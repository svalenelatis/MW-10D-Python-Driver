This is a lightweight Python driver for the MW-10D Variable Impedence Match device. 

It runs over serial and an RS422 connection. We will be controlling it with a custom-made driver in Python, that will run on a Raspberry Pi. For testing, we're using a spare arduino
with a loosely made copy of the device's communications.

Our focus is safety, and a layer of separation between user commands and device commands, with strong lockouts and an eventual password system.

We will make multiple versions of this driver, scoped to different folders. This allows us to reach workable checkpoints for the research team to continue development, but allow us to improve over time the usability and safety of the driver.

The scoping is as follows.

V0: The debugger. Sends a one-shot ACT readback request every 200 ms (configurable) and directly dumps the raw serial response. Defaults to /dev/ttyUSB0 at 9600 baud, 8-N-1.

V1: The super basic wrapper. Defaults to raw listening mode, sending ACT- and directly dumping serial output to the terminal. Polling mode sends one-shot ACT requests every 200 ms by default (configurable). Type t and press Enter to toggle modes.


V1.5: Safe super basic wrapped. Same as above, but first submits commands to ensure device is in local control, then automatic tuning mode. 


V2: A better controller all together. Fully stateful, GUI based. Password and safety lock-outs. Upon booting, checks for connectivity, and retries in set loops on failures. When a reboot event is seen by the presence of the bootup message, logs the reboot and resets the state for safety. If link and readback is good, it then figures out state of mode and preset, and updates its internals state to match.
System settings are able to be changed, but otherwise, default to Local Automatic, and poll for specific device settings. Several buttons and presets are available on the screen, including tuning presets and specific readbacks or diagnostics. 
Gracefully drops link and tries to re-establish if needed.

V3: Controller is built into a custom housing, with GPIO pins used for physical buttons. Rotary encoders/potentiomters are used for when, and only when, manual tuning mode is safe by state and explictly allowed. All actions are double checked when submitted. Include a auto-tune safety lockout where needed. 


Resources:

We're working with an MW-10D Variable Impedence Match, with a controller box. The controller box has a remote interface 24 pin connector, configured for RS422. We will be controlling it via serial and ASCII with a Raspberry Pi.

In the repository, - 1015-001.pdf is the instruction manual provided. Review when deciding between best practices for the official documentation.

