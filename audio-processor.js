class PcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const mono = inputs[0] && inputs[0][0];
    if (mono && mono.length) this.port.postMessage(new Float32Array(mono));
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
