// k6 load test for POST /api/v1/agent/query (design.md Appendix C).
// Arrival model: constant arrival rate, so offered load does not drop when latency rises.
// Select a tier with -e TIER=5k|10k|50k. The default is a short local smoke run.
import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const TIER = __ENV.TIER || 'local';
const RATE = { local: 20, '5k': 500, '10k': 1000, '50k': 8500 }[TIER];
if (RATE === undefined) {
  throw new Error(`unknown TIER=${TIER}`);
}

export const options = {
  scenarios: {
    query_load: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: TIER === 'local' ? '30s' : '10m',
      preAllocatedVUs: Math.ceil(RATE * 0.5),
      maxVUs: RATE * 2,
    },
  },
  thresholds: {
    http_req_duration: ['p(50)<240', 'p(95)<450', 'p(99)<590'],
    http_req_failed: ['rate<0.0005'],
    checks: ['rate>0.999'],
  },
};

const QUERIES = [
  'Verify batch LOT-88392 safety protocol',
  'What is the 50K load target?',
  'Explain the human-in-the-loop rule for Tier 3 mutations',
];

export default function () {
  const vu = __VU;
  const tenant = 'tenant_demo';
  const body = JSON.stringify({
    tenant_id: tenant,
    user_id: `load_user_${vu}`,
    query: QUERIES[vu % QUERIES.length],
  });
  const res = http.post(`${BASE_URL}/api/v1/agent/query`, body, {
    headers: { 'Content-Type': 'application/json' },
  });
  check(res, {
    'status is 200 or 429': (r) => r.status === 200 || r.status === 429,
    'tenant is echoed': (r) => r.status !== 200 || r.json('tenant_id') === tenant,
  });
}
