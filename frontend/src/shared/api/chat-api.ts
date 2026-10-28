import { request } from './http-client';

export interface ChatReply {
  answer: string;
}

export const chatApi = {
  chat: (message: string) => request<ChatReply>('/chat', {
    method: 'POST',
    body: JSON.stringify({ message }),
  }, {
    timeoutMs: 90_000,
    networkErrorMessage: '聊天请求失败或超时，请检查网络后重新发送。重新发送会发起新的模型请求。',
  }),
};
