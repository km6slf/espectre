/* ESPectre - Shared mDNS Bootstrap Responder Stub for ESP-IDF 5.3 */
#include "mdns_bootstrap_responder.h"

namespace espectre {

MdnsBootstrapResponder::~MdnsBootstrapResponder() = default;

bool MdnsBootstrapResponder::setup() {
  configured_.store(true);
  return true;
}

bool MdnsBootstrapResponder::update(uint32_t ipv4_address) {
  ipv4_address_.store(ipv4_address);
  return true;
}

void MdnsBootstrapResponder::loop() {
}

void MdnsBootstrapResponder::shutdown() {
  configured_.store(false);
  ipv4_address_.store(0U);
}

void MdnsBootstrapResponder::ingest_query(const uint8_t *packet, size_t length, size_t interface, uint32_t source_ipv4, uint16_t source_port) {
}

}  // namespace espectre
