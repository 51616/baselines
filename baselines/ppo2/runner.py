import numpy as np
from baselines.common.runners import AbstractEnvRunner

class Runner(AbstractEnvRunner):
    """
    We use this object to make a mini batch of experiences
    __init__:
    - Initialize the runner

    run():
    - Make a mini batch
    """
    def __init__(self, *, env, model, nsteps, gamma, lam):
        super().__init__(env=env, model=model, nsteps=nsteps)
        # Lambda used in GAE (General Advantage Estimation)
        self.lam = lam
        # Discount rate
        self.gamma = gamma

    def run(self):
        # Here, we init the lists that will contain the mb of experiences
        mb_obs, mb_rewards, mb_actions, mb_values, mb_dones, mb_neglogpacs = [],[],[],[],[],[]
        mb_states = self.states
        epinfos = []
        # For n in range number of steps

        import time
        tot_time = time.time()
        int_time = 0
        num_envs = len(self.curr_state)

        if self.env.trajectory_sp:
            # Selecting which environments should run fully in self play
            sp_envs_bools = np.random.random(num_envs) < self.env.self_play_randomization
            print("SP envs: {}/{}".format(sum(sp_envs_bools), num_envs))

        other_agent_simulation_time = 0
        for _ in range(self.nsteps):

            # Given observations, get action value and neglopacs
            # We already have self.obs because Runner superclass run self.obs[:] = env.reset() on init
            overcooked = 'env_name' in self.env.__dict__.keys() and self.env.env_name == "Overcooked-v0"
            if overcooked:
                actions, values, self.states, neglogpacs = self.model.step(self.obs0, S=self.states, M=self.dones)

                if not self.env.joint_action_model:
                    import time
                    current_simulation_time = time.time()
                    
                    if self.env.other_agent_true:
                        p_self_play = self.env.self_play_randomization

                        if self.env.trajectory_sp:

                            if sum(sp_envs_bools) != num_envs: # Compute BC actions if some num of envs not running in BC
                                # Get actions through the action method of the agent
                                from hr_coordination.mdp.overcooked_mdp import Action
                                other_agent_actions_bc = self.env.other_agent.action(self.curr_state, self.other_agent_idx)
                                other_agent_actions_bc = [Action.ACTION_TO_INDEX[a] for a in other_agent_actions_bc]

                            if sum(sp_envs_bools) != 0: # Compute SP actions if some num of envs is supposed to run in SP
                                other_agent_actions_sp, _, _, _ = self.model.step(self.obs1, S=self.states, M=self.dones)

                            other_agent_actions = []
                            for i in range(num_envs):
                                if sp_envs_bools[i]:
                                    sp_action = other_agent_actions_sp[i]
                                    other_agent_actions.append(sp_action)
                                else:
                                    bc_action = other_agent_actions_bc[i]
                                    other_agent_actions.append(bc_action)
                        
                        else:
                            other_agent_actions = np.zeros_like(self.curr_state)

                            if p_self_play < 1:
                                # Get actions through the action method of the agent
                                from hr_coordination.mdp.overcooked_mdp import Action
                                other_agent_actions = self.env.other_agent.action(self.curr_state, self.other_agent_idx)
                                other_agent_actions = [Action.ACTION_TO_INDEX[a] for a in other_agent_actions]

                            ####### Naive non-parallelized way of getting actions for other ########
                            
                            if p_self_play > 0:
                                self_play_actions, _, _, _ = self.model.step(self.obs1, S=self.states, M=self.dones)
                                self_play_bools = np.random.random(num_envs) < p_self_play

                                # Currently randomizes at the step level, might want to randomize at the trajectory level
                                # That is harder to do though, as requires dealing with dones
                                for i in range(num_envs):
                                    is_self_play_action = self_play_bools[i]
                                    if is_self_play_action:
                                        other_agent_actions[i] = self_play_actions[i]

                    elif self.env.other_agent_bc:
                        # Parallelise actions with direct action, using the featurization function
                        featurized_states = [self.env.mdp.featurize_state(s, self.env.mlp) for s in self.curr_state]
                        player_featurizes_states = [s[idx] for s, idx in zip(featurized_states, self.other_agent_idx)]
                        other_agent_actions = self.env.other_agent.direct_policy(player_featurizes_states, sampled=True, no_wait=True)
                    else:
                        other_agent_actions = self.env.other_agent.direct_action(self.obs1)

                    other_agent_simulation_time += time.time() - current_simulation_time

                    joint_action = [(actions[i], other_agent_actions[i]) for i in range(len(actions))]
                    
                else:
                    joint_action = actions

                mb_obs.append(self.obs0.copy())
            else:
                actions, values, self.states, neglogpacs = self.model.step(self.obs, S=self.states, M=self.dones)
                mb_obs.append(self.obs.copy())

            mb_actions.append(actions)
            mb_values.append(values)
            mb_neglogpacs.append(neglogpacs)
            mb_dones.append(self.dones)

            # Take actions in env and look the results
            # Infos contains a ton of useful informations
            if overcooked:
                both_obs_and_state_and_other_idx, rewards, self.dones, infos = self.env.step(joint_action)

                # PROCESSING TODO: clean, same as runner in /common
                ########## START CLEAN UP ######## 
                transp_shape = list(range(len(both_obs_and_state_and_other_idx.shape)))
                transp_shape[0] = 1
                transp_shape[1] = 0
                both_obs_and_state_and_other_idx = np.transpose(both_obs_and_state_and_other_idx, transp_shape)
                
                both_obs, state_and_other_idx = both_obs_and_state_and_other_idx
                state_and_other_idx = np.array(state_and_other_idx)
                both_obs = np.array(both_obs)

                threads, players = np.array(both_obs).shape
                
                obs = []
                for y in range(players):
                    sub_obs = []
                    for x in range(threads):
                        sub_obs.append(both_obs[x][y])
                    obs.append(sub_obs)

                obs0, obs1 = obs
                # print(np.array(obs0).shape)
                # print(np.array(obs1).shape)
                # # both_obs = np.hstack(both_obs)
                
                # print([np.array(a).shape for a in both_obs])
                # print(both_obs.shape)
                # print(both_obs)
                self.obs0[:] = np.array(obs0) #np.concatenate(both_obs[:,0])
                self.obs1[:] = np.array(obs1) #np.concatenate(both_obs[:,1])
                self.curr_state, self.other_agent_idx = state_and_other_idx[:, 0], state_and_other_idx[:, 1]
                ##################### END CLEAN UP ######
            else:
                self.obs[:], rewards, self.dones, infos = self.env.step(actions)

            for info in infos:
                maybeepinfo = info.get('episode')
                if maybeepinfo: epinfos.append(maybeepinfo)
            mb_rewards.append(rewards)

        print("Other agent actions took", other_agent_simulation_time, "seconds")
        tot_time = time.time() - tot_time
        print("Total simulation time for {} steps: {} \t Other agent action time: {} \t {} steps/s".format(self.nsteps, tot_time, int_time, self.nsteps / tot_time))
        
        #batch of steps to batch of rollouts
        mb_obs = np.asarray(mb_obs, dtype=self.obs.dtype)
        mb_rewards = np.asarray(mb_rewards, dtype=np.float32)
        mb_actions = np.asarray(mb_actions)
        mb_values = np.asarray(mb_values, dtype=np.float32)
        mb_neglogpacs = np.asarray(mb_neglogpacs, dtype=np.float32)
        mb_dones = np.asarray(mb_dones, dtype=np.bool)
        last_values = self.model.value(self.obs, S=self.states, M=self.dones)

        # discount/bootstrap off value fn
        mb_returns = np.zeros_like(mb_rewards)
        mb_advs = np.zeros_like(mb_rewards)
        lastgaelam = 0
        for t in reversed(range(self.nsteps)):
            if t == self.nsteps - 1:
                nextnonterminal = 1.0 - self.dones
                nextvalues = last_values
            else:
                nextnonterminal = 1.0 - mb_dones[t+1]
                nextvalues = mb_values[t+1]
            delta = mb_rewards[t] + self.gamma * nextvalues * nextnonterminal - mb_values[t]
            mb_advs[t] = lastgaelam = delta + self.gamma * self.lam * nextnonterminal * lastgaelam
        mb_returns = mb_advs + mb_values
        return (*map(sf01, (mb_obs, mb_returns, mb_dones, mb_actions, mb_values, mb_neglogpacs)),
            mb_states, epinfos)
# obs, returns, masks, actions, values, neglogpacs, states = runner.run()
def sf01(arr):
    """
    swap and then flatten axes 0 and 1
    """
    s = arr.shape
    return arr.swapaxes(0, 1).reshape(s[0] * s[1], *s[2:])



def switch_player(obs):
    assert len(obs.shape) == 3
    obs = obs.copy()
    obs = switch_layers(obs, 0, 1)
    obs = switch_layers(obs, 2, 6)
    obs = switch_layers(obs, 3, 7)
    obs = switch_layers(obs, 4, 8)
    obs = switch_layers(obs, 5, 9)
    return obs

def switch_layers(obs, idx0, idx1):
    obs = obs.copy()
    tmp = obs[:,:,idx0].copy()
    obs[:,:,idx0] = obs[:,:,idx1]
    obs[:,:,idx1] = tmp
    return obs