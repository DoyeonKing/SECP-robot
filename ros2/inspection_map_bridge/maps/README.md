# Map assets

Copy the Yahboom map pair into this directory before building:

    yahboomcar.yaml
    yahboomcar.pgm

Keep the PGM bytes unchanged. Change only the copied YAML image entry so it is
deployable outside the original robot workspace:

    image: yahboomcar.pgm
