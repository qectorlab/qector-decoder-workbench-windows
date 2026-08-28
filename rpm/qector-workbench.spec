Name:           qector-workbench
Version:        1.0.4
Release:        1%{?dist}
Summary:        QECTOR Decoder Workbench - Enterprise Quantum Error Correction Suite

License:        Proprietary (Source-Available, Academic & Research)
URL:            https://www.qector.store
Source0:        %{name}-%{version}.tar.gz

BuildArch:      x86_64
BuildRequires:  python3-devel, python3-pip, python3-setuptools, python3-wheel
Requires:       python3, python3-tkinter, xorg-x11-server-Xvfb

%description
QECTOR Decoder Workbench v1.0.4 (Linux x86_64) is an enterprise-grade Quantum Error Correction research environment featuring:
- Rust/PyO3 C++ Extension Core (qector_decoder_v3) for high-throughput syndrome decoding.
- 85-Tool Model Context Protocol (MCP) Server operating under strict air-gapped isolation on Linux.
- Zero-Egress AST Attestation & Runtime EgressGuard socket blocking non-loopback connections.
- 633 unit/integration tests and executive proof verification.
- CustomTkinter Multi-tab GUI: Code Explorer, Decoder Lab, Benchmarking, and Diagnostics.

%prep
%setup -q

%build
# Source files ready for distribution

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}/opt/qector-workbench
cp -a * %{buildroot}/opt/qector-workbench/

mkdir -p %{buildroot}%{_bindir}
cat << 'EOF' > %{buildroot}%{_bindir}/qector-workbench
#!/bin/sh
export PYTHONPATH="/opt/qector-workbench:$PYTHONPATH"
exec python3 /opt/qector-workbench/main.py "$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/qector-workbench

mkdir -p %{buildroot}%{_datadir}/applications
cat << 'EOF' > %{buildroot}%{_datadir}/applications/qector-workbench.desktop
[Desktop Entry]
Name=QECTOR Decoder Workbench
Comment=Quantum Error Correction Decoder Suite with 85-tool MCP Server
Exec=qector-workbench
Icon=qector-workbench
Terminal=false
Type=Application
Categories=Development;Science;Quantum;
EOF

mkdir -p %{buildroot}%{_datadir}/icons/hicolor/256x256/apps
cp assets/icon_256.png %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/qector-workbench.png

%files
/opt/qector-workbench
%{_bindir}/qector-workbench
%{_datadir}/applications/qector-workbench.desktop
%{_datadir}/icons/hicolor/256x256/apps/qector-workbench.png

%changelog
* Thu Aug 27 2026 Guillaume Lessard <admin@qector.store> - 1.0.4-1
- Release 1.0.4 with multi-platform Linux distribution support and zero-egress security.
