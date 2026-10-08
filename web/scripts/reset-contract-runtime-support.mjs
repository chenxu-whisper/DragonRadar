export function composeOverride({
  publicPort,
  ownerPort,
  publicEdgeSubnet,
}) {
  for (const port of [publicPort, ownerPort]) {
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      throw new Error(`Invalid ephemeral port: ${port}`);
    }
  }
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
      - "127.0.0.1:${publicPort}:8080"
    networks:
      public-edge:
        ipv4_address: ${prefix}.2

  owner-caddy:
    environment:
      DRAGON_OWNER_AUTHORITY: localhost:${ownerPort}
    ports: !override
      - "127.0.0.1:${ownerPort}:8443"

  owner-api:
    command:
      - serve-owner
      - --host
      - 127.0.0.1
      - --port
      - "8001"
      - --origin
      - "https://localhost:${ownerPort}"
    environment:
      DRAGON_OWNER_AUTHORITY: localhost:${ownerPort}

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
