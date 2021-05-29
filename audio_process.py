#!/usr/bin/python3

from os.path import splitext;
from typing import List;
import numpy as np;
from pydub import AudioSegment;
from scipy.io import wavfile;
from librosa import note_to_hz, cqt, cqt_frequencies;
from librosa.beat import beat_track;
import pyaudio;
import struct;

class AudioProcess(object):
  __opened = False;
  def __init__(self, audio_path = None):
    if audio_path is not None:
      self.load(audio_path);
  @property
  def data(self):
    return self.__data;
  @property
  def sample_width(self):
    return self.__sample_width;
  @property
  def channels(self):
    return self.__channels;
  @property
  def frame_rate(self):
    return self.__frame_rate;
  def load(self, audio_path):
    audiofile = AudioSegment.from_file(audio_path);
    # 1) data
    self.__data = np.reshape(np.array(audiofile.get_array_of_samples()), (-1, audiofile.channels)); # self.data.shape = (sample_num, channel_num)
    # 2) attributes
    self.__sample_width = audiofile.sample_width; # how many bytes for one sample
    self.__channels = audiofile.channels; # how many sound channels (whether it is a stereo audio)
    self.__frame_rate = audiofile.frame_rate; # how many samples per second
    # 3) flag to represent whether a file has been loaded
    self.__opened = True;
  def normalize(self, data: np.array = None):
    if self.__opened == False:
      raise Exception('load an audio file first!');
    if data is None: data = self.__data;
    # return data in range [-1, 1]
    return data / 2**(8*self.__sample_width - 1);
  def denormalize(self, data):
    if self.__opened == False:
      raise Exception('load an audio file first!');
    return (data * 2**(8*self.__sample_width - 1)).astype(self.__data.dtype);
  def slice(self, start: int, length: int, normalized: bool = False, output: str = None):
    if self.__opened == False:
      raise Exception('load an audio file first!');
    data = self.normalize() if normalized else self.__data;
    retval = data[start*self.__frame_rate:(start+length)*self.__frame_rate,:];
    if output is not None:
      assert splitext(output)[1] == '.wav';
      wavfile.write(output, self.__frame_rate, retval);
    return retval;
  def split(self, length: int, normalized: bool = False, output: str = None):
    if self.__opened == False:
      raise Exception('load an audio file first!');
    data = self.normalize() if normalized else self.__data;
    segment_size = length * self.__frame_rate; # how many samples per slice
    retval = [data[x:x+segment_size,:] for x in np.arange(0, data.shape[0], segment_size)];
    if output is not None:
      assert splitext(output)[1] == '.wav';
      wavfile.write(output, self.__frame_rate, retval);
    return retval;
  def split_channels(self, output: str = None):
    if self.__opened == False:
      raise Exception('load an audio file first!');
    channels = np.split(self.__data, self.__channels, -1); # channels = list[sample number x 1]
    if output is not None:
      assert splitext(output)[1] == '.wav';
      for i, channel in enumerate(channels):
        wavfile.write(splitext(output)[0] + str(i) + splitext(output)[1], channel, channel);
    return channels;
  def join_channels(self, channels: List[np.array], output: str = None):
    if self.__opened == False:
      raise Exception('load an audio file first!');
    retval = np.concatenate(channels, axis = -1); # retval.shape = (sample number, channels)
    if output is not None:
      assert splitext(output)[1] == '.wav';
      wavfile.write(output, self.__frame_rate, retval);
    return retval;
  def remove_silent_part(self, output: str = None):
    if self.__opened == False:
      raise Exception('load an audio file first!');
    if output is None:
      output = "generated.wav";
    slices = self.split(1, True);
    energies = np.array([np.mean(slice**2, axis = 0) for slice in slices]); # energies.shape = (slice num, channel)
    thres = 0.5 * np.median(energies, axis = 0); # thres.shape = (channel,)
    index_of_segments_to_keep = np.where(np.logical_and.reduce(energies > thres, axis = 1));
    picked_slices = [self.denormalize(slices[i]) for i in index_of_segments_to_keep[0]];
    data = np.concatenate(picked_slices, axis = 0); # data.shape = (sample number, channel)
    wavfile.write(output, self.__frame_rate, data);
  def get_tempo(self, just_beats = False):
    if self.__opened == False:
      raise Exception('load an audio file first!');
    tempo_channels = list();
    # 1) create frames representing a beat which lasts for 0.2 second
    samples = np.arange(0, 0.2, 1 / self.__frame_rate); # how many frames for a beat
    amp_mod = 0.2 / (np.sqrt(samples) + 0.2) - 0.2; # amplitude decay, range in [-0.2, 0.8]
    amp_mod[amp_mod < 0] = 0; # filter sub-zero part, range in [0, 0.8]
    x = np.max(self.__data) * np.cos(2 * np.pi * samples * 220) * amp_mod; # generate samples with scaled amplitude
    # 2) generate audio frames containing beats which is as long as the loaded audio
    beat_channels = list();
    for i in range(self.__data.shape[1]):
      # detect beats for every single channel of the loaded audio
      # NOTE: beats is a list of time (seconds) which are picked as beats for tempo
      tempo, beats = beat_track(self.__data[:,i].astype(np.float32),  sr = self.__frame_rate, units="time");
      beat_channels.append(beats);
      #beats -= 0.05;
      tempo_channel = np.zeros_like(self.__data[:,i]); # temp_channel.shape = (sample number)
      for ib, b in enumerate(beats):
        sample_periods = np.arange(0, 0.2, 1 / self.__frame_rate);
        amp_mod = 0.2 / (np.sqrt(sample_periods) + 0.2) - 0.2; # amplitude decay, range in [-0.2, 0.8]
        amp_mod[amp_mod < 0] = 0; # filter sub-zero part, range in [0, 0.8]
        x = np.max(self.__data) * np.cos(2 * np.pi * sample_periods * 220) * amp_mod;
        tempo_channel[int(self.__frame_rate * b): int(self.__frame_rate * b) + int(x.shape[0])] = x.astype(np.int16);
      tempo_channels.append(np.expand_dims(tempo_channel, axis = -1));
    return tempo_channels if just_beats == False else beat_channels;
  def from_microphone(self, sample_window: float = 0.2, frame_rate: int = 8000, channels: int = 1, count: int = -1):
    # sample_window: how long (second) each sample segment is
    pa = pyaudio.PyAudio();
    stream = pa.open(format = pyaudio.paInt16, channels = channels, rate = frame_rate, input = True, frames_per_buffer = int(frame_rate * sample_window));
    i = 0;
    while True if count < 0 else i < count:
      block = stream.read(int(frame_rate * sample_window)); # get samples from microphone
      shorts = struct.unpack("%dh" % (len(block) / 2), block);
      data = np.array(list(short)).astype(np.int16);
      i += 1;
    # TODO: save audio from microphone to self
  def cqt(self, data: np.array = None, hop_lengths: List[int] = None, bins_per_octave: int = 12):
    # data: shape = (sample number, channel number)
    # hop_length: how many samples are between two selected sample segments
    if self.__opened == False:
      raise Exception('load an audio file first!');
    if hop_lengths is None:
      hop_lengths = [512] * self.__channels;
    assert len(hop_lengths) == self.__channels;
    normalized = self.normalize(data);
    channels = list();
    for i in range(self.__channels):
      normalized_channel = normalized[:,i];
      channel_results = cqt(normalized_channel, self.__frame_rate, hop_lengths[i], fmin = note_to_hz('A0'), n_bins = 88, bins_per_octave = bins_per_octave); # results.shape = (84, hop number)
      channels.append(channel_results);
    spectrum = np.stack(channels, axis = 0); # spectrum.shape = (channel number, 88, hop number)
    freqs = cqt_frequencies(88, fmin = note_to_hz('A0'), bins_per_octave = bins_per_octave);
    return spectrum, freqs;
  def scale_recognition(self,):
    if self.__opened == False:
      raise Exception('load an audio file first!');
    beat_channels = self.get_tempo(just_beats = True);
    hop_lengths = list();
    start_frames = list();
    for channel, beats in enumerate(beat_channels):
      hop_length = int(np.mean(beats[1:] - beats[:-1]) * self.__frame_rate);
      hop_lengths.append(hop_length);
      start_frame = int(beats[0] * self.__frame_rate - hop_length / 2) if beats[0] * self.__frame_rate - hop_length / 2 > 0 else int(beats[0] * self.__frame_rate + hop_length / 2);
      start_frames.append(start_frame);
    hop_lengths = [np.mean(hop_lengths).astype(np.int32)] * self.__channels;
    start_frame = np.mean(start_frames).astype(np.int32);
    data = np.concatenate([self.__data[start_frame:,:], self.__data[:start_frame,:]], axis = 0);
    spectrum, freqs = self.cqt(data, hop_lengths);

if __name__ == "__main__":

  ap = AudioProcess('samples/talk.mp3');
  print(ap.sample_width);
  print(ap.channels);
  print(ap.frame_rate);
  normalized = ap.normalize();
  sliced = ap.slice(2,2);
  splitted = ap.split(1000);
  ap.remove_silent_part();
  ap.load('samples/brahms_lullaby.mp3');
  channels = ap.split_channels();
  tempo_channels = ap.get_tempo();
  for i,(c,t) in enumerate(zip(channels, tempo_channels)):
    ap.join_channels([c,t], str(i) + ".wav");
  #ap.from_microphone(count = 10);
  ap.scale_recognition();
