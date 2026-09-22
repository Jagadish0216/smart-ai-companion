from .models import DeviceCommand, CompanionState

class BaseDeviceController:
    """
    Abstract interface for controlling the physical (or mock) companion hardware.
    """
    def set_expression(self, expression: str):
        raise NotImplementedError

    def set_eye_style(self, style: str):
        raise NotImplementedError

    def set_animation(self, animation: str):
        raise NotImplementedError

    def set_display_text(self, text: str):
        raise NotImplementedError

    def set_brightness(self, level: int):
        raise NotImplementedError
        
    def set_state(self, state: str):
        raise NotImplementedError


class MockDeviceController(BaseDeviceController):
    """
    Simulates sending commands to the Raspberry Pi 5.
    Logs actions to the DeviceCommand model.
    """
    def _log_command(self, cmd_type: str, payload: dict):
        cmd = DeviceCommand.objects.create(
            command_type=cmd_type,
            payload=payload,
            status='SIMULATED'
        )
        return cmd

    def set_expression(self, expression: str):
        state = CompanionState.get_current()
        state.expression = expression
        state.save(update_fields=['expression', 'updated_at'])
        return self._log_command("SET_EXPRESSION", {"expression": expression})

    def set_eye_style(self, style: str):
        state = CompanionState.get_current()
        state.eye_style = style
        state.save(update_fields=['eye_style', 'updated_at'])
        return self._log_command("SET_EYE_STYLE", {"style": style})

    def set_animation(self, animation: str):
        state = CompanionState.get_current()
        state.animation = animation
        state.save(update_fields=['animation', 'updated_at'])
        return self._log_command("SET_ANIMATION", {"animation": animation})

    def set_display_text(self, text: str):
        state = CompanionState.get_current()
        state.display_text = text
        state.save(update_fields=['display_text', 'updated_at'])
        return self._log_command("SET_DISPLAY_TEXT", {"text": text})

    def set_brightness(self, level: int):
        state = CompanionState.get_current()
        state.brightness = level
        state.save(update_fields=['brightness', 'updated_at'])
        return self._log_command("SET_BRIGHTNESS", {"level": level})

    def set_state(self, state_val: str):
        state = CompanionState.get_current()
        state.state = state_val
        state.save(update_fields=['state', 'updated_at'])
        return self._log_command("SET_STATE", {"state": state_val})

# Global instance for use in views
device = MockDeviceController()
