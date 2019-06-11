# Copyright (C) 2018 Heron Systems, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import abc
from time import time

import numpy as np
import torch


class CountsRewards(abc.ABC):
    """
    Maintains a buffer that increments and resets episode rewards for multiple
    environments.
    """
    # define lazy properties
    _episode_reward_buffer = None
    _local_step_count = 0
    _start_time = time()

    # ABSTRACT PROPS
    @property
    @abc.abstractmethod
    def nb_env(self):
        raise NotImplementedError

    # DEFINED PROPS
    @property
    def local_step_count(self):
        return self._local_step_count

    @local_step_count.setter
    def local_step_count(self, step_count):
        self._local_step_count = step_count

    @property
    def start_time(self):
        return self._start_time

    @start_time.setter
    def start_time(self, st):
        self._start_time = st

    @property
    def episode_reward_buffer(self):
        if self._episode_reward_buffer is None:
            self._episode_reward_buffer = torch.zeros(self.nb_env)
        return self._episode_reward_buffer

    @episode_reward_buffer.setter
    def episode_reward_buffer(self, updated_buffer):
        self._episode_reward_buffer = updated_buffer

    def update_buffers(self, rewards, terminals, infos):
        self.episode_reward_buffer += torch.tensor(rewards).float()
        self.local_step_count += self.nb_env
        terminal_rewards = []
        terminal_infos = []
        for ep_reward, done, info in zip(
            self.episode_reward_buffer, terminals, infos
        ):
            if done and info:
                terminal_rewards.append(ep_reward.item())
                terminal_infos.append((info))
                ep_reward.zero_()
        return terminal_rewards, terminal_infos

    def set_local_step_count(self, step_count):
        self.local_step_count = step_count


class LogsRewards(CountsRewards, metaclass=abc.ABCMeta):
    @property
    @abc.abstractmethod
    def logger(self):
        raise NotImplementedError

    def log_episode_results(
        self,
        terminal_rewards,
        terminal_infos,
        global_step_count,
        local_step_count=None,
        rank=None,
        initial_step_count=0
    ):

        if terminal_rewards:
            if local_step_count is None:
                local_step_count = global_step_count

            ep_reward = np.mean(terminal_rewards)
            if rank is None:
                delta_t = time() - self.start_time
                self.logger.info(
                    'STEP: {} REWARD: {} STEP/S: {}'.format(
                        global_step_count,
                        ep_reward,
                        (local_step_count - initial_step_count) / delta_t
                    )
                )
            else:
                delta_t = time() - self.start_time
                self.logger.info(
                    'RANK: {} '
                    'GLOBAL STEP: {} '
                    'REWARD: {} '
                    'GLOBAL STEP/S: {} '
                    'LOCAL STEP/S: {}'.format(
                        rank,
                        global_step_count,
                        ep_reward,
                        (global_step_count - initial_step_count) / delta_t,
                        (local_step_count - initial_step_count) / delta_t
                    )
                )
        return terminal_rewards


class LogsAndSummarizesRewards(LogsRewards, metaclass=abc.ABCMeta):
    @property
    @abc.abstractmethod
    def summary_writer(self):
        raise NotImplementedError

    @property
    def summary_name(self):
        return 'reward/train'

    def write_reward_summaries(self, terminal_rewards, step_count):
        if terminal_rewards:
            ep_reward = np.mean(terminal_rewards)
            self.summary_writer.add_scalar(
                self.summary_name, ep_reward, step_count
            )
        return terminal_rewards


class WritesSummaries(abc.ABC):
    _prev_summary_time = time()

    # ABSTRACT PROPS
    @property
    @abc.abstractmethod
    def network(self):
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def summary_writer(self):
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def summary_frequency(self):
        raise NotImplementedError

    # DEFINED PROPS
    @property
    def prev_summary_time(self):
        return self._prev_summary_time

    @prev_summary_time.setter
    def prev_summary_time(self, pst):
        self._prev_summary_time = pst

    def write_summaries(self, total_loss, loss_dict, metric_dict, step_count):
        cur_time = time()
        elapsed = cur_time - self.prev_summary_time

        if elapsed > self.summary_frequency:
            self.prev_summary_time = cur_time

            writer = self.summary_writer
            writer.add_scalar(
                'macro_loss/total_loss', total_loss.item(), step_count
            )
            for l_name, loss in loss_dict.items():
                writer.add_scalar('loss/' + l_name, loss.item(), step_count)
            for m_name, metric in metric_dict.items():
                if len(metric.shape) <= 1:
                    writer.add_scalar('metric/' + m_name, metric.item(), step_count)
                # image data
                if len(metric.shape) == 3:
                    writer.add_image(m_name, metric, step_count)
            for p_name, param in self.network.named_parameters():
                p_name = p_name.replace('.', '/')
                writer.add_scalar(p_name, torch.norm(param).item(), step_count)
                if param.grad is not None:
                    writer.add_scalar(
                        p_name + '.grad',
                        torch.norm(param.grad).item(), step_count
                    )


class SavesModels(abc.ABC):
    _next_save = 0

    @property
    def next_save(self):
        return self._next_save

    @next_save.setter
    def next_save(self, step_count):
        self._next_save = step_count

    @property
    @abc.abstractmethod
    def epoch_len(self):
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def network(self):
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def optimizer(self):
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def saver(self):
        raise NotImplementedError

    def save_model_if_epoch(self, step_count):
        """
        :param step_count: current step count to check if epoch has been crossed
        :return:
        """
        if step_count >= self.next_save:
            self.saver.save_state_dicts(
                self.network, int(step_count), optimizer=self.optimizer
            )
            self.next_save += self.epoch_len

    def set_next_save(self, initial_count):
        if initial_count > 0:
            while self.next_save <= initial_count:
                self.next_save += self.epoch_len


class HasAgent(abc.ABC):
    @property
    @abc.abstractmethod
    def agent(self):
        raise NotImplementedError

    @property
    def network(self):
        return self.agent.network

    @property
    def exp_cache(self):
        return self.agent.exp_cache


class HasEnvironment(abc.ABC):
    @property
    @abc.abstractmethod
    def environment(self):
        raise NotImplementedError


class MPIProc:
    def receive(self):
        raise NotImplementedError

    def submit(self):
        raise NotImplementedError


class AppliesGrads(abc.ABC):
    @property
    @abc.abstractmethod
    def optimizer(self):
        raise NotImplementedError

    @abc.abstractmethod
    def apply_gradients(self, gradients):
        raise NotImplementedError
