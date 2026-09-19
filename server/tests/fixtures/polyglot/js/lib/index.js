const { double } = require('./helper');
const express = require('express');

class Server {
  start(port) {
    for (let i = 0; i < 2; i++) {
      double(port);
    }
  }
}

export default function main() {
  express();
  return new Server().start(8080);
}
