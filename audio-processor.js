class PcmRing extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.offset = 0;
    this.played = 0;
    this.reportAt = 2400;
    this.active = false;
    this.port.onmessage = event => {
      const data = event.data;
      if (data && data.length) {
        this.queue.push(data);
        this.active = true;
      }
      if (data && data.type === "clear") {
        this.queue = [];
        this.offset = 0;
        this.played = 0;
        this.reportAt = 2400;
        this.active = false;
        this.port.postMessage({type: "played", samples: 0});
      }
    };
  }
  process(_inputs, outputs) {
    const out = outputs[0][0];
    let consumed = 0;
    for (let i = 0; i < out.length; i++) {
      while (this.queue.length && this.offset >= this.queue[0].length) {
        this.queue.shift();
        this.offset = 0;
      }
      if (this.queue.length) {
        out[i] = this.queue[0][this.offset++];
        consumed++;
      } else {
        out[i] = 0;
      }
    }
    this.played += consumed;
    if (this.played >= this.reportAt) {
      this.port.postMessage({type: "played", samples: this.played});
      this.reportAt = this.played + 2400;
    }
    if (this.active && this.queue.length === 0) {
      this.active = false;
      this.port.postMessage({type: "drained", samples: this.played});
    }
    return true;
  }
}

class PcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (input && input.length) this.port.postMessage(new Float32Array(input));
    return true;
  }
}

registerProcessor("pcm-ring", PcmRing);
registerProcessor("pcm-capture", PcmCapture);
