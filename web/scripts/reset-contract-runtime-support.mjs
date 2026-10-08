export function composeOverride({
  publicEdgeSubnet,
}) {
  const subnetMatch = publicEdgeSubnet?.match(
    /^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.0\/28$/,
  );
  if (
    !subnetMatch
    || subnetMatch.slice(1).some(octet => Number(octet) > 255)
  ) {
    throw new Error(`Invalid public edge subnet: ${publicEdgeSubnet}`);
  }
  const prefix = subnetMatch.slice(1).join('.');
  return `services:
  public-api:
    environment:
      DRAGON_TRUSTED_PROXY_CIDR: ${prefix}.2/32
    networks:
      public-edge:
        ipv4_address: ${prefix}.3

  public-caddy:
    ports: !override
      - target: 8080
        published: "0"
        host_ip: 127.0.0.1
        protocol: tcp
    networks:
      public-edge:
        ipv4_address: ${prefix}.2

  owner-caddy:
    environment:
      DRAGON_OWNER_AUTHORITY: localhost:8443
    ports: !override
      - target: 8443
        published: "0"
        host_ip: 127.0.0.1
        protocol: tcp

  owner-api:
    command:
      - serve-owner
      - --host
      - 127.0.0.1
      - --port
      - "8001"
      - --origin
      - "https://localhost:8443"
    environment:
      DRAGON_OWNER_AUTHORITY: localhost:8443

networks:
  public-edge:
    ipam:
      config: !override
        - subnet: ${publicEdgeSubnet}
`;
}

export function parseLoopbackBinding(output) {
  const match = output.trim().match(/^127\.0\.0\.1:([0-9]+)$/);
  const port = match ? Number(match[1]) : 0;
  if (port < 1 || port > 65535) {
    throw new Error(
      `Could not parse a loopback published port from: ${JSON.stringify(output)}`,
    );
  }
  return { host: '127.0.0.1', port };
}

export function ownerTransportRequestOptions({ connectPort, ca }) {
  return {
    hostname: '127.0.0.1',
    port: connectPort,
    path: '/api/owner/v1/session',
    method: 'GET',
    servername: 'localhost',
    ca,
    headers: {
      Host: 'localhost:8443',
    },
    timeout: 10_000,
  };
}
