import time
from typing import Any, Dict, Protocol

import numpy as np


class Agent(Protocol):
    def act(self, obs: Dict[str, Any]) -> np.ndarray:
        """Returns an action given an observation.

        Args:
            obs: observation from the environment.

        Returns:
            action: action to take on the environment.
        """
        raise NotImplementedError

    def move_to_position(self, position: np.ndarray) -> bool:
        """Move the robot to a given position.

        Args:
            position: position to move the robot to.

        Returns:
            bool: True if successful, False if not.
        """

    def set_torque_mode(self, mode: bool):
        """Set the torque mode of the robot.

        Args:
            mode: True if torque mode, False if position mode.
        """
        raise NotImplementedError

    def close(self):
        """Close the agent.
        """
        raise NotImplementedError


class DummyAgent(Agent):
    def __init__(self, num_dofs: int):
        self.num_dofs = num_dofs

    def act(self, obs: Dict[str, Any]) -> np.ndarray:
        return np.zeros(self.num_dofs)


class BimanualAgent(Agent):
    def __init__(self, agent_left: Agent, agent_right: Agent):
        self.agent_left = agent_left
        self.agent_right = agent_right

    def act(self, obs: Dict[str, Any]) -> np.ndarray:
        left_obs = {}
        right_obs = {}
        for key, val in obs.items():
            L = val.shape[0]
            half_dim = L // 2
            assert L == half_dim * 2, f"{key} must be even, something is wrong"
            left_obs[key] = val[:half_dim]
            right_obs[key] = val[half_dim:]
        return np.concatenate(
            [[0] * 7, self.agent_right.act(right_obs)]
        )

    def move_to_position(self, position: np.ndarray) -> bool:
        time.sleep(0.05)
        self.agent_right.move_to_position(np.array(position[7:]))

        time.sleep(0.05)
        self.agent_left.move_to_position(np.array(position[:7]))
        
        return True

    def set_torque_mode(self, mode: bool):
        self.agent_left.set_torque_mode(mode)
        self.agent_right.set_torque_mode(mode)

    def close(self):
        self.agent_left.close()
        self.agent_right.close()