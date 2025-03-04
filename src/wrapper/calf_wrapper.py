import numpy as np
import torch as th

from gymnasium import Wrapper
from stable_baselines3.common.logger import configure
from copy import copy


class RelaxProb:
    """
    A generic class used for Relax Probability decay 
    """
    def __init__(self):
        self.relax_prob = ...

    def reset(self):
        ...

    def step(self):
        ...

    def get_relax_prob(self) -> float:
        """
        Returns the current value of relax_prob.
        
        Returns: The current relax_prob value.
        """
        return self.relax_prob


class RelaxProbLinear(RelaxProb):
    """
    The RelaxProb class manages the relaxation probability with a mechanism for linearly decaying it 
    over a specified number of time steps. This is particularly useful in scenarios 
    where a gradual reduction in relaxation probability is required, 
    such as in reinforcement learning or optimization algorithms. 

    NOTE: At this moment, the RelaxProb only supports the decay of relax_prob over time_step. It is not aware of the change to a new episode.
    """
    def __init__(self, initial_value: float, total_steps: int):
        """
        Initializes the RelaxProb class.

        Parameters:
            initial_value: The initial value of relax_prob.
            total_steps: The total number of time steps over which relax_prob will reduce to 0.
        """
        if initial_value < 0 or total_steps <= 0:
            raise ValueError("initial_value must be non-negative and total_steps must be positive.")

        self.total_steps = total_steps
        self.initial_value = initial_value
        self.reset()

    def reset(self):
        self.current_step = 0
        self.relax_prob = self.initial_value

    def step(self):
        """
        Reduces the relax_prob value linearly for each time step until it reaches 0.
        """
        if self.current_step < self.total_steps:
            decrement = self.initial_value / self.total_steps
            self.relax_prob = max(0, self.relax_prob - decrement)
            self.current_step += 1

    
class RelaxProbExponetial(RelaxProb):
    """
    The RelaxProb class manages the relaxation probability with a mechanism for exponential decaying it 
    over a specified number of time steps. This is particularly useful in scenarios 
    where a significant reduction in relaxation probability at earlier steps is required, 
    such as in reinforcement learning or optimization algorithms. 
    """
    def __init__(self, 
                 initial_relax_prob: float, 
                 relax_prob_base_step_factor: float,
                 relax_prob_episode_factor: float):
        """
        Initializes the RelaxProb class.

        Parameters:
            initial_relax_prob: The initial value of relax_prob.
            relax_prob_base_step_factor: Relax prob at the 1st episode and 1st step
            relax_prob_episode_factor: Factor to update relax_prob_step_factor at the end of each episode
        """
        if initial_relax_prob < 0:
            raise ValueError("initial_relax_prob must be non-negative.")

        # Relax prob at the 1st episode and 1st step
        self.relax_prob_step_factor = relax_prob_base_step_factor

        # Factor to update relax_prob_step_factor at the end of each episode
        self.relax_prob_episode_factor = relax_prob_episode_factor

        # increase after each episode (episode to episode)
        self.initial_relax_prob = initial_relax_prob
        self.relax_prob = initial_relax_prob
        self.current_step = 0

    def reset(self):
        if self.current_step != 0:
            self.initial_relax_prob = np.clip(
                self.initial_relax_prob + self.initial_relax_prob * self.relax_prob_episode_factor,
                0, 1)
            
        self.relax_prob = self.initial_relax_prob
        self.current_step = 0

    def step(self):
        """
        Reduces the relax_prob value linearly for each time step until it reaches 0.
        """
        self.relax_prob = max(0, self.relax_prob * self.relax_prob_step_factor)
        self.current_step += 1


class CALFNominalWrapper():
    """
    A generic class used for CALFWrapper fallback 
    """
    def __init__(self, controller):
        self.controller = controller

    def compute_action(self, observation):
        action = ...
        return action 


class CALFWrapper(Wrapper):
    """
    Description: This CALF wrapper filters actions from the action 
        and apply a fallback action to the environment by utilizing CALF condition.
    Note: This environment is used only outside of a normal environment 
        (not a vectorized environment).
    """
    def __init__(self, 
                 env, 
                 relax_decay: RelaxProb,
                 fallback_policy: CALFNominalWrapper = None, 
                 calf_decay_rate: float = 0.0005,
                 **kwargs):
        super().__init__(env)
        self.last_good_value = None
        self.current_value = None
        self.fallback_policy = fallback_policy
        self.calf_activated_count = 0
        self.calf_decay_count = 0
        self.calf_relax_prob_fires_count = 0
        self.calf_decay_rate = calf_decay_rate

        # Actual relax prob
        self.relax_decay = relax_decay
        self.relax_decay.reset()
        self.relax_prob = self.relax_decay.get_relax_prob()

        self.logger = kwargs.get("logger", configure())
        self.debug = kwargs.get("debug", False)

        self.policy_model = None

    def copy_policy_model(self, policy_model):
        self.policy_model = copy(policy_model)

    def get_relax_prob(self):
        self.relax_decay.step()
        return self.relax_decay.get_relax_prob()

    def get_state_value(self, state):
        with th.no_grad():
            return self.policy_model.predict_values(
                self.policy_model.obs_to_tensor(state)[0]
            )
        
    def _step(self, action):
        return self.env.step(action)

    def step(self, agent_action):
        current_value = self.get_state_value(self.current_obs)
        
        if_calf_constraint_satisfied = current_value - self.last_good_value >= self.calf_decay_rate
        
        if if_calf_constraint_satisfied:
            self.last_good_value = current_value
            self.calf_decay_count += 1
            self.logger.record("calf/calf_decay_count", self.calf_decay_count)
        
        if_relax_prob_fires = np.random.random() < self.relax_prob

        if if_relax_prob_fires:
            self.calf_relax_prob_fires_count += 1
            self.logger.record("calf/calf_relax_prob_fires_count", self.calf_decay_count)

        if if_calf_constraint_satisfied or if_relax_prob_fires:
            action = agent_action
            self.calf_activated_count += 1
            self.logger.record("calf/calf_activated_count", self.calf_activated_count)
            self.debug and print("[DEBUG]: Line 12")
            
        else:
            action = self.fallback_policy.compute_action(self.current_obs)
            self.debug and print("[DEBUG]: Line 14")                
        
        # Update relax probability
        self.debug and print("[DEBUG]: Line 16")
        self.relax_prob = self.get_relax_prob()

        self.current_obs, reward, terminated, truncated, info = self._step(
            action
        )
        self.debug and print("[DEBUG]: Line 5")
        
        self.logger.record("calf/last_relax_prob", self.relax_prob)

        return self.current_obs.copy(), float(reward), terminated, truncated, info
    
    def reset_internal_params(self):
        # Reset Relax decay mechanism
        self.relax_decay.reset()
        self.relax_prob = self.relax_decay.get_relax_prob()

        self.last_good_value = self.get_state_value(self.current_obs)
        self.calf_activated_count = 0
        self.calf_decay_count = 0
        self.calf_relax_prob_fires_count = 0

        self.logger.record("calf/init_relax_prob", self.relax_prob)

    def reset(self, **kwargs):
        self.current_obs, info = self.env.reset(**kwargs)
        self.reset_internal_params()
        
        return self.current_obs.copy(), info


class CALFWrapperSingleVecEnv(CALFWrapper):
    """
    Note: 
        This wrapper can be used outside of a Vectorized Environment
        and its relax probability would be updated each step using the class RelaxProb
    """

    def reset(self, **kwargs):
        returns = self.env.reset(**kwargs)
        if len(self.env.reset(**kwargs)) == 2:
            self.current_obs, info = returns
        else:
            self.current_obs = returns
            print(type(returns))
            info = None

        self.reset_internal_params()
        
        return self.current_obs.copy(), info

    def _step(self, action):
        returns = self.env.step(
            action
        )
        if len(returns) == 5:
            obs, reward, terminated, truncated, info = returns
        else:
            obs, reward, dones, infos = returns
            info = infos[0]
            terminated = False
            truncated = False
            for idx, done in enumerate(dones):
                if done:
                    terminated = "terminal_observation" in infos[idx]
                    truncated = infos[idx].get("TimeLimit.truncated", False)

        return obs, reward, terminated, truncated, info

    def step(self, agent_action):
        results = super().step(agent_action)

        # Only for logging
        current_value = self.get_state_value(self.current_obs)
        self.logger.record("calf/state_value", current_value[0][0].cpu().numpy())
        self.logger.record("calf/CALF_value", self.last_good_value[0][0].cpu().numpy())
        
        return results
        