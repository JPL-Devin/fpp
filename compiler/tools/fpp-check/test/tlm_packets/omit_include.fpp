deployment topology T1 {

  instance c1
  instance c2
  instance c3
  instance c4

  telemetry packets P1 {

    packet P1 id 0 group 0 {
      c1.T
      include "included_channels.fppi"
    }

  } omit {
    c3.T
    include "omitted_channels.fppi"
  }

}
