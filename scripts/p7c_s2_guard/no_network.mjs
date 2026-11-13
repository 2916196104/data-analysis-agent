// Offline unit tests must never reach a real API, PostgreSQL, Redis or paid service.
import net from 'node:net';
net.Socket.prototype.connect = function () { throw new Error('P7-C offline: network connection forbidden'); };
